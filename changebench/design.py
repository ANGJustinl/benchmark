"""Experimental planning and paired, task-clustered descriptive inference."""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from .catalog import TASKS
from .policy import policy_hash


def source_fingerprint() -> str:
    root=Path(__file__).parent
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.rglob("*") if p.is_file() and p.suffix in {".py",".txt",".json"}}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


DEFAULT_BUDGETS = {
    "1": {"wall_seconds":180,"total_tokens":30000,"tool_calls":100},
    "2": {"wall_seconds":300,"total_tokens":60000,"tool_calls":140},
    "3": {"wall_seconds":480,"total_tokens":90000,"tool_calls":180},
    "4": {"wall_seconds":720,"total_tokens":120000,"tool_calls":240},
}


def make_plan(repeats: int = 2, seed: int = 20261015, conditions=("A", "B", "C"),
              tasks=None, budgets=None, evaluation=None, budget_profile=None,
              client_profile=None, persistent_upgrade=False) -> dict:
    from itertools import permutations
    from .registry import metadata, ALL_TASKS
    if type(repeats) is not int or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if not conditions or len(set(conditions)) != len(conditions) or not set(conditions) <= {"A", "B", "C"}:
        raise ValueError("Invalid conditions")
    tasks=tuple(TASKS if tasks is None else tasks)
    if not tasks or len(set(tasks))!=len(tasks) or not set(tasks)<=set(ALL_TASKS):
        raise ValueError("Invalid task selection")
    if type(persistent_upgrade) is not bool: raise ValueError("persistent_upgrade must be Boolean")
    profile=json.loads(json.dumps(budget_profile or {"name":"legacy-responses-v1","calibration_status":"uncalibrated"}))
    if set(profile)!={"name","calibration_status"} or not isinstance(profile["name"],str) or not profile["name"]:
        raise ValueError("Named, versioned budget profile required")
    if profile["calibration_status"] not in {"uncalibrated","calibrated"}:
        raise ValueError("Explicit calibration status required")
    limits=json.loads(json.dumps(DEFAULT_BUDGETS if budgets is None else budgets))
    if set(limits)!={"1","2","3","4"}: raise ValueError("Exactly four level budgets required")
    evaluation=json.loads(json.dumps(evaluation or {"random_count":18,"execution_seconds":8,
                                                   "compile_seconds":120,"startup_seconds":30}))
    if set(evaluation)!={"random_count","execution_seconds","compile_seconds","startup_seconds"}:
        raise ValueError("Complete evaluation limits required")
    if any(type(v) is not int or v<=0 for v in evaluation.values()):
        raise ValueError("Evaluation limits must be positive integers")
    client_profile=json.loads(json.dumps(client_profile or {}))
    if not isinstance(client_profile,dict) or set(client_profile)-{"model_id","reasoning_effort","artifacts"}:
        raise ValueError("Unsupported client profile fields; never include credentials")
    for key in ("model_id","reasoning_effort"):
        if key in client_profile and (not isinstance(client_profile[key],str) or not client_profile[key]):
            raise ValueError("Client identifiers must be nonempty strings")
    artifacts=client_profile.get("artifacts",{})
    if not isinstance(artifacts,dict): raise ValueError("Client artifacts must map absolute paths to SHA256")
    for path,sha in artifacts.items():
        if not isinstance(path,str) or not Path(path).is_absolute() or not isinstance(sha,str) or len(sha)!=64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("Invalid client artifact fingerprint")
    for level in ("1","2","3","4"):
        if set(limits.get(level,{}))!={"wall_seconds","total_tokens","tool_calls"}:
            raise ValueError("Four complete level budgets required")
        if any(type(v) is not int or v<=0 for v in limits[level].values()):
            raise ValueError("Budgets must be positive integers")
    rng = random.Random(seed)
    trials=[];blocks=[(t,r) for t in tasks for r in range(repeats)];rng.shuffle(blocks)
    orders=list(permutations(conditions));rng.shuffle(orders)
    allocation_orders={};allocation_counts=defaultdict(int)
    # Sharing seeds across language ports permits contract parity checks; ports are correlated.
    seeds={}
    for index,(task,repeat) in enumerate(blocks):
        meta=metadata(task);key=(meta["track"],meta["family"],repeat)
        if key not in seeds:seeds[key]=(rng.randrange(1,2**31),rng.randrange(1,2**31))
        eval_seed,generation_seed=seeds[key]
        stratum=(meta["track"],meta["language"],meta["level"])
        if stratum not in allocation_orders:
            allocation_orders[stratum]=list(orders);rng.shuffle(allocation_orders[stratum])
        order=allocation_orders[stratum][allocation_counts[stratum]%len(orders)]
        allocation_counts[stratum]+=1
        for position,condition in enumerate(order):
            trials.append({"trial_id":f"{task}-{repeat}-{condition}","task":task,
                "repeat":repeat,"condition":condition,"family":meta["family"],"language":meta["language"],
                "level":meta["level"],"track":meta["track"],"block_id":f"{task}-{repeat}",
                "block_position":position,"evaluation_seed":eval_seed,"generation_seed":generation_seed})
    plan={"schema_version":3,"benchmark_version":"0.3.0","seed":seed,"repeats":repeats,
          "conditions":list(conditions),"task_ids":list(tasks),"branches":{"local":0.5,"extension":0.5},
          "evaluation":evaluation,"budget_profile":profile,"client_profile":client_profile,
          "persistent_upgrade":persistent_upgrade,
          "budgets_by_level":limits,"design_body_sha256":policy_hash(),"source_sha256":source_fingerprint(),
          "task_metadata":{t:metadata(t) for t in tasks},"trials":trials,
          "estimand":"paired treatment effect within language and task; family-clustered",
          "suite_visibility":"public authored calibration; not an uncontaminated private holdout"}
    plan["plan_sha256"]=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
    return plan


def validate_plan(plan: dict) -> None:
    copy=dict(plan);fingerprint=copy.pop("plan_sha256",None)
    if hashlib.sha256(json.dumps(copy,sort_keys=True).encode()).hexdigest()!=fingerprint:
        raise ValueError("Plan hash mismatch")
    if plan.get("schema_version")!=3:
        raise ValueError("Older run plans require their original harness, not silent conversion")
    if plan.get("source_sha256")!=source_fingerprint():
        raise ValueError("Benchmark source drift detected; freeze a new plan")
    if plan.get("design_body_sha256")!=policy_hash():raise ValueError("Policy drift detected")
    # Reconstruct exact intended allocation, metadata, seeds, budgets and ordering.
    expected=make_plan(plan["repeats"],plan["seed"],plan["conditions"],plan["task_ids"],plan["budgets_by_level"],
                       plan["evaluation"],plan["budget_profile"],plan["client_profile"],plan["persistent_upgrade"])
    if plan!=expected:raise ValueError("Plan schema, pairing, order or metadata differs from frozen design")


def paired_bootstrap(rows: list[dict], a: str, b: str, field: str, seed: int = 29,
                     resamples: int = 2000) -> dict:
    """B minus A; resample task families, not individual branch observations."""
    grouped = defaultdict(dict)
    for row in rows:
        if row["condition"] in {a, b}:
            key = (row["task"], row["repeat"])
            if row["condition"] in grouped[key]:
                raise ValueError("Duplicate comparison cell")
            value = row[field]
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Finite comparison values required")
            grouped[key][row["condition"]] = value
    if not grouped or any(set(x) != {a, b} for x in grouped.values()):
        raise ValueError("Paired comparison requires complete cells")
    clusters = defaultdict(list)
    for (task, _), values in grouped.items():
        from .registry import ALL_TASKS, metadata
        family = (metadata(task)["track"],metadata(task)["family"]) if task in ALL_TASKS else task
        clusters[family].append(values[b] - values[a])
    means = [sum(vals)/len(vals) for vals in clusters.values()]
    if resamples < 2:
        raise ValueError("At least two resamples required")
    rng = random.Random(seed)
    boots = sorted(sum(rng.choices(means, k=len(means)))/len(means) for _ in range(resamples))
    return {"comparison": f"{b}-{a}", "field": field, "difference": sum(means)/len(means),
            "ci95_exploratory": [boots[int(.025*(resamples-1))], boots[int(.975*(resamples-1))]],
            "task_clusters": len(means), "warning": "Resamples task families, not language ports or repeated runs; small authored samples do not support universal claims."}
