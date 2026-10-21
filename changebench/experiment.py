"""Paired experiment runner with fresh, neutral maintenance sessions."""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .catalog import initial_files, public_spec
from .design import validate_plan
from .registry import metadata as task_metadata, POLY_TASKS
from .runtimes import runtime_manifest, preflight_runtime_images
from .execution import clean_env, evaluate, run_bounded, strict_json, resolve_docker_image, cleanup_worker_containers, docker_binary
from .fixtures import reference_files
from .policy import condition_payload
from .workspace import digest, diff_diagnostics, materialize, sanitized, validate_files

BUDGET = {"wall_seconds": 300, "total_tokens": 60000, "tool_calls": 120}


@dataclass
class AgentResult:
    files: dict[str, str]
    status: str
    usage: dict
    metadata: dict
    evidence_kind: str


class Adapter(Protocol):
    model_id: str
    def run(self, request: dict) -> AgentResult: ...


def blank_usage() -> dict:
    return {"input_tokens": None, "output_tokens": None, "cached_input_tokens": None,
            "reasoning_tokens": None, "tool_calls": None}


def validate_usage(usage: dict) -> dict:
    if not isinstance(usage, dict):
        raise ValueError("usage must be an object")
    result = blank_usage()
    for key in result:
        value = usage.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("Token/tool counters must be nonnegative integers or null")
        result[key] = value
    for subset, total in (("cached_input_tokens", "input_tokens"), ("reasoning_tokens", "output_tokens")):
        if result[subset] is not None and result[total] is not None and result[subset] > result[total]:
            raise ValueError("Breakdown cannot exceed total; do not double-count tokens")
    return result


def total_tokens(usage: dict) -> int | None:
    if usage.get("input_tokens") is None or usage.get("output_tokens") is None:
        return None
    return usage["input_tokens"] + usage["output_tokens"]


class ReplayAdapter:
    """Deterministic fixture playback. No reasoning or maintenance cost is measured."""
    model_id = "reference-fixture"
    def run(self, request: dict) -> AgentResult:
        layout = "modular" if request["generation_seed"] % 2 else "flat"
        return AgentResult(reference_files(request["task_key"], request["stage"], layout),
                           "completed", blank_usage(), {"model_id": self.model_id,
                           "skill_events": [], "note": "Fixture playback, not a model trial."}, "fixture")


class CommandAdapter:
    """Trusted worker executable. Never launch an LLM-written executable here."""
    def __init__(self, config: dict, repo: Path | None = None, allow_fixture: bool = False):
        self.config = config
        self.repo = repo or Path(__file__).resolve().parents[1]
        self.model_id = config["model_id"]
        if not isinstance(self.model_id, str) or not self.model_id:
            raise ValueError("Explicit model_id required")
        command = config["command"]
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise ValueError("command must be an argv array, never a shell string")
        self.command = [s.replace("{python}", sys.executable).replace("{repo}", str(self.repo)).replace("{worker}", str(Path(__file__).with_name("worker_main.py"))) for s in command]
        self.allow_fixture = allow_fixture

    def run(self, request: dict) -> AgentResult:
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="changebench-agent-") as temp:
            root = Path(temp)
            env = clean_env(temp)
            for key in self.config.get("pass_env", []):
                if key in os.environ:
                    env[key] = os.environ[key]
            settings = dict(self.config.get("settings", {}))
            if request.get("tool_image"):
                settings["docker_image"] = request["tool_image"]
            request = {**request, "adapter_settings": settings}
            if request.get("tool_image"):
                env["CHANGEBENCH_DOCKER_EXECUTABLE"] = docker_binary()
            session = uuid.uuid4().hex
            env["CHANGEBENCH_RUN_ID"] = session
            cleanup_warning = None
            try:
                process = run_bounded(self.command, json.dumps(request).encode(), root,
                                      timeout=request["budget"]["wall_seconds"], output_limit=4_000_000, env=env)
            finally:
                if request.get("tool_image"):
                    cleanup_warning = cleanup_worker_containers(session)
        if process.status != "ok":
            status = "infra_error" if process.status == "launch_error" else process.status
            return AgentResult({}, status, blank_usage(), {"process": process.__dict__,
                               "model_id": self.model_id}, "live_model")
        try:
            response = strict_json(process.stdout)
            if not isinstance(response, dict) or response.get("schema_version") != 1:
                raise ValueError("Unsupported adapter response")
            kind = response["evidence_kind"]
            if kind != "live_model" and not (self.allow_fixture and kind == "fixture"):
                raise ValueError("Fixture results cannot masquerade as live results")
            if response["model_id"] != self.model_id:
                raise ValueError("Requested and reported model identifiers differ")
            usage = validate_usage(response["usage"])
            status = response.get("status", "completed")
            if status not in {"completed", "budget_exhausted", "infra_error", "model_error"}:
                raise ValueError("Unknown adapter status")
            files = validate_files(response["files"]) if response.get("files") else {}
            tokens = total_tokens(usage)
            if (tokens is not None and tokens > request["budget"]["total_tokens"] or
                usage["tool_calls"] is not None and usage["tool_calls"] > request["budget"]["tool_calls"]):
                status = "budget_exhausted"
            return AgentResult(files, status, usage,
                               {"model_id": self.model_id, "skill_events": response.get("skill_events", []),
                                "api_calls": response.get("api_calls", []),
                                "known_usage": response.get("known_usage"),
                                "verification_events": response.get("verification_events", []),
                                "adapter_wall_seconds": time.monotonic()-start,
                                "cleanup_warning": cleanup_warning,
                                "seed_supported": response.get("seed_supported", False),
                                "error": response.get("error")}, kind)
        except (KeyError, TypeError, ValueError) as exc:
            return AgentResult({}, "invalid_response", blank_usage(),
                               {"error": str(exc), "model_id": self.model_id}, "live_model")


def make_request(trial: dict, stage: str, files: dict, model_id: str, budget: dict | None = None) -> dict:
    maintenance = stage != "initial"
    policy = condition_payload(trial["condition"], maintenance)
    return {**({"language": task_metadata(trial["task"])["language"], "protocol": "poly-jsonl"} if trial["task"] in POLY_TASKS else {}),
            "schema_version": 1, "model_id": model_id,
            "task_key": trial["task"], "stage": stage,
            "public_task": public_spec(trial["task"], stage),
            "files": sanitized(files), **policy,
            "generation_seed": trial["generation_seed"] + {"initial":0,"local":1,"extension":2}[stage],
            "budget": dict(budget or BUDGET)}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement preserves the last complete checkpoint on interruption.
    temp=path.with_name(path.name+".tmp-"+uuid.uuid4().hex)
    try:
        with temp.open("w",encoding="utf-8") as stream:
            stream.write(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+"\n")
            stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def run_experiment(plan: dict, builder: Adapter, maintainer: Adapter, out: Path,
                   backend: str = "docker", trusted_local: bool = False,
                   image: str | dict = "python:3.13-slim", budget: dict | None = None) -> list[dict]:
    validate_plan(plan)
    if budget is not None and any(budget != v for v in plan["budgets_by_level"].values()):
        raise ValueError("Budget overrides must be frozen in the plan before execution")
    profile=plan["client_profile"]
    for adapter in (builder,maintainer):
        if profile.get("model_id",adapter.model_id)!=adapter.model_id:
            raise ValueError("Frozen client model differs from adapter")
        effort=getattr(adapter,"config",{}).get("settings",{}).get("reasoning_effort")
        if "reasoning_effort" in profile and profile["reasoning_effort"]!=effort:
            raise ValueError("Frozen reasoning effort differs from adapter")
    for path,sha in profile.get("artifacts",{}).items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=sha:
            raise ValueError("Frozen client artifact changed: "+path)
    limits = dict(budget or BUDGET)
    runtime_before = runtime_manifest()
    language_images = {}
    runtime_preflight = {"status":"not_run_trusted_fixture_playback"}
    for name in ("wall_seconds", "total_tokens", "tool_calls"):
        if type(limits.get(name)) not in (int, float) or not math.isfinite(limits[name]) or limits[name] <= 0:
            raise ValueError("Positive finite budgets required")
    if backend == "local" and not trusted_local:
        raise ValueError("Explicit trusted-local permission required")
    if backend == "docker":
        import shutil
        if not shutil.which("docker"):
            raise RuntimeError("Docker unavailable; cannot start an isolated live evaluation")
        languages={task_metadata(t)["language"] for t in plan["task_ids"]}
        if isinstance(image,str) and languages!={"python"}:
            raise ValueError("Multi-language run requires evaluation_images per language")
        requested = image if isinstance(image,dict) else {"python":image}
        if not languages <= requested.keys():raise ValueError("Missing language image; refuse partial trial allocation")
        for lang in sorted(languages):language_images[lang]=resolve_docker_image(requested[lang])
        runtime_preflight = {"status":"passed","languages":preflight_runtime_images(language_images,
            execution_timeout=plan["evaluation"]["execution_seconds"],compile_timeout=plan["evaluation"]["compile_seconds"],
            startup_timeout=plan["evaluation"]["startup_seconds"])}
        if plan["persistent_upgrade"] and any(task_metadata(t).get("persistent") for t in plan["task_ids"]):
            if "python" not in language_images and "state" not in requested:
                raise ValueError("Disk handoff requires an explicit trusted Python image under evaluation_images.state")
            language_images["state"]=language_images.get("python") or resolve_docker_image(requested["state"])
            preflight_runtime_images({"python":language_images["state"]})
    if out.exists() and any(out.iterdir()):
        raise ValueError("Output directory must be new/empty; refusing accidental overwrite or cherry-picked resume")
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "plan.json", plan)
    write_json(out / "environment.json", {"python": sys.version, "platform": platform.platform(),
        "backend": backend, "docker_images": language_images, "runtime_manifest": runtime_before, "runtime_preflight": runtime_preflight,
        "builder_model_id": builder.model_id, "maintainer_model_id": maintainer.model_id,
        "adapters": {role: {"class": type(adapter).__name__,
            "config_sha256": hashlib.sha256(json.dumps(getattr(adapter, "config", {}), sort_keys=True).encode()).hexdigest(),
            "public_settings": {k:v for k,v in getattr(adapter, "config", {}).get("settings", {}).items()
                                if k in {"reasoning_effort", "docker_image"}},
            "environment_variable_names": getattr(adapter, "config", {}).get("pass_env", [])}
            for role,adapter in (("builder",builder),("maintainer",maintainer))},
        "budgets_by_level": plan["budgets_by_level"], "fresh_session_each_stage": True,
        "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in Path(__file__).parent.glob("*.py")}})
    write_json(out / "RUN-STATE.json", {"status":"running","plan_sha256":plan["plan_sha256"]})
    rows = []
    with (out / "records.jsonl").open("w", encoding="utf-8") as log:
        for trial in plan["trials"]:
            record = {**trial, "plan_sha256": plan["plan_sha256"], "stages": {}}
            initial = initial_files(trial["task"])
            initial_passed = False
            t0_snapshot = None
            upgrade_seed = None
            for stage in ("initial", "local", "extension"):
                if stage != "initial" and not initial_passed:
                    record["stages"][stage] = {"status": "blocked_by_initial", "success": False,
                        "usage": blank_usage(), "generation_wall_seconds": 0.0, "invoked": False}
                    continue
                source = initial if stage == "initial" else t0_snapshot
                adapter = builder if stage == "initial" else maintainer
                stage_budget = plan["budgets_by_level"][str(task_metadata(trial["task"])["level"])]
                request = make_request(trial, stage, source, adapter.model_id, stage_budget)
                if backend == "docker":
                    request["tool_image"] = language_images[task_metadata(trial["task"])["language"]]
                if plan["persistent_upgrade"] and task_metadata(trial["task"]).get("persistent"):
                    request["public_task"] += ("\nUPGRADE COMPATIBILITY CONTRACT (v0.3 opt-in): Maintenance must read the actual "
                        "disk produced by your initial version, preserve values, versions, successful-id receipts and consumed messages, "
                        "and continue operating across a second process restart. Use regular files/directories only; no links or special files. "
                        "Total persisted file data <=256KiB, at most 200 files and 256 directories.")
                request["checkpoint_path"]=str((out/"checkpoints"/trial["trial_id"]/(stage+".json")).resolve())
                input_path=f"inputs/{trial['trial_id']}/{stage}.json"
                write_json(out/input_path,source)
                write_json(out/"progress.json",{"completed_trials":len(rows),"trial":record,"active_stage":stage})
                before = time.monotonic()
                try:
                    result = adapter.run(request)
                except Exception as exc:
                    result = AgentResult({}, "infra_error", blank_usage(), {"error": repr(exc)}, "unknown")
                elapsed = time.monotonic() - before
                checkpoint=Path(request['checkpoint_path'])
                if not result.files and checkpoint.is_file():
                    try:
                        recovered=strict_json(checkpoint.read_text())
                        if recovered:result.files=sanitized(recovered)
                        result.metadata['checkpoint_recovered']=True
                    except (ValueError,TypeError):pass
                tokens=total_tokens(result.usage)
                calls=result.usage.get("tool_calls")
                if result.status=="completed" and ((tokens is not None and tokens>stage_budget["total_tokens"]) or
                    (calls is not None and calls>stage_budget["tool_calls"]) or elapsed>stage_budget["wall_seconds"]):
                    result.status="budget_exhausted"
                evidence = record.setdefault("evidence_kind", result.evidence_kind)
                if result.evidence_kind != evidence:
                    result.status = "infra_error"
                    result.metadata["error"] = "Mixed fixture and model evidence in a trajectory"
                item = {"status": result.status, "success": False, "invoked": True,
                        "usage": result.usage, "generation_wall_seconds": elapsed, "budget": stage_budget,
                        "metadata": result.metadata, "input_snapshot_sha256": digest(source),"input_artifact_path":input_path}
                if result.metadata.get("receipt_path"):
                    receipt=Path(result.metadata["receipt_path"])
                    relative=f"evidence/{trial['trial_id']}/{stage}-receipt.json"
                    write_json(out/relative,strict_json(receipt.read_text()))
                    item["metadata"]["receipt_path"]=relative
                item["generation_status"]=result.status
                item["budget_excess_tokens"]=max(0,tokens-stage_budget["total_tokens"]) if tokens is not None else None
                # Files are saved before scoring, including legitimate empty/partial workspaces.
                try:
                    snapshot=sanitized(result.files) if result.files else {}
                    submitted=result.status=="completed" and bool(snapshot)
                    relative=(f"snapshots/{trial['trial_id']}/{stage}.json" if submitted else
                              f"artifacts/{trial['trial_id']}/{stage}.json")
                    write_json(out/relative,snapshot)
                    item["artifact"]={"path":relative,"kind":"submitted" if submitted else "partial",
                        "sha256":hashlib.sha256((out/relative).read_bytes()).hexdigest()}
                    if submitted:item["output_snapshot_sha256"]=digest(snapshot)
                except (ValueError,TypeError) as exc:
                    snapshot={};item["artifact"]={"kind":"unavailable","reason":str(exc)}
                    if result.status=="completed":item["status"]="invalid_submission"
                if result.status == "completed" and item["status"] == "completed":
                    try:
                        validate_files(snapshot)
                        item["output_snapshot_sha256"] = digest(snapshot)
                        item["diff_diagnostics"] = diff_diagnostics(source, snapshot)
                        evaluation = evaluate(snapshot, trial["task"], stage, trial["evaluation_seed"],
                            backend=backend, trusted_local=trusted_local, image=language_images.get(task_metadata(trial["task"])["language"]),
                            random_count=plan["evaluation"]["random_count"], timeout=plan["evaluation"]["execution_seconds"],
                            compile_timeout=plan["evaluation"]["compile_seconds"],startup_timeout=plan["evaluation"]["startup_seconds"])
                        item["evaluation"] = evaluation
                        item["success"] = evaluation["success"]
                        item["status"] = evaluation["status"]
                        if stage == "initial":
                            t0_snapshot = snapshot
                            initial_passed = item["success"]
                    except (ValueError, TypeError) as exc:
                        item["status"], item["error"] = "invalid_submission", str(exc)
                    except RuntimeError as exc:
                        item["status"], item["error"] = "infra_error", str(exc)
                if plan["persistent_upgrade"] and task_metadata(trial["task"]).get("persistent") and item["success"]:
                    from .upgrade import create_seed, check_upgrade, StateFormatError
                    try:
                        if stage=="initial":
                            upgrade_seed=create_seed(snapshot,trial,out,backend,language_images,plan["evaluation"],trusted_local)
                            item["upgrade_seed"]=upgrade_seed
                            if not upgrade_seed["success"]:
                                item["success"]=False;item["status"]=upgrade_seed["status"];initial_passed=False
                        else:
                            upgrade=check_upgrade(snapshot,trial,stage,out,upgrade_seed,backend,language_images,
                                                  plan["evaluation"],trusted_local)
                            item["upgrade"]=upgrade
                            item["success"]=upgrade["success"]
                            if not upgrade["success"]:item["status"]=upgrade["status"]
                    except StateFormatError as exc:
                        item["success"]=False;item["status"]="invalid_submission";item["upgrade_error"]=str(exc)
                        if stage=="initial":initial_passed=False
                    except (OSError,RuntimeError,ValueError) as exc:
                        item["success"]=False;item["status"]="infra_error";item["upgrade_error"]=str(exc)
                        if stage=="initial":initial_passed=False
                record["stages"][stage] = item
                write_json(out/"progress.json",{"completed_trials":len(rows),"trial":record,"active_stage":None})
            record["initial_success"] = bool(record["stages"]["initial"]["success"])
            record["local_success"] = record["initial_success"] and record["stages"]["local"]["success"]
            record["extension_success"] = record["initial_success"] and record["stages"]["extension"]["success"]
            record["joint_success"] = record["local_success"] and record["extension_success"]
            log.write(json.dumps(record, ensure_ascii=True, allow_nan=False)+"\n")
            log.flush();os.fsync(log.fileno())
            rows.append(record)
    for path,sha in profile.get("artifacts",{}).items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=sha:
            raise ValueError("Frozen client artifact changed during run: "+path)
    write_json(out / "runtime-after.json", runtime_manifest())
    write_json(out / "RUN-STATE.json", {"status":"completed","plan_sha256":plan["plan_sha256"]})
    from .fairness import seal_run
    seal_run(out)
    return rows
