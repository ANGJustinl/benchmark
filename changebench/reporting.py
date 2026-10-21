"""No architecture composite. Failures stay in denominators; missing data stays missing."""
from __future__ import annotations

import csv
import hashlib
import json
import random
from pathlib import Path

from .design import paired_bootstrap, validate_plan
from .catalog import public_spec
from .experiment import total_tokens, write_json
from .workspace import materialize, sanitized


def summarize(plan: dict, rows: list[dict]) -> dict:
    validate_plan(plan)
    ids = [r["trial_id"] for r in rows]
    expected = {t["trial_id"] for t in plan["trials"]}
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError("Missing, duplicated, or unexpected trials: no selective denominator")
    intended = {t["trial_id"]: t for t in plan["trials"]}
    for r in rows:
        if r.get("plan_sha256") != plan["plan_sha256"]:
            raise ValueError("Result belongs to another plan")
        if any(r.get(k) != intended[r["trial_id"]][k] for k in ("task","repeat","condition")):
            raise ValueError("Trial metadata mismatch")
        for phase, stage in r["stages"].items():
            if type(stage.get("success")) is not bool:
                raise ValueError("Stage success must be Boolean")
            if "evaluation" in stage:
                from .fairness import check_evaluation
                check_evaluation(stage["evaluation"],stage,r["task"],phase,plan)
        if set(r["stages"]) != {"initial", "local", "extension"}:
            raise ValueError("Stage omissions must be explicit, not silently dropped")
        initial = bool(r["stages"]["initial"]["success"])
        local = initial and bool(r["stages"]["local"]["success"])
        extension = initial and bool(r["stages"]["extension"]["success"])
        if any(r.get(k) != v for k,v in {"initial_success":initial,"local_success":local,
               "extension_success":extension,"joint_success":local and extension}.items()):
            raise ValueError("Cached success fields disagree with stage evidence")
    kinds = {r["evidence_kind"] for r in rows}
    if len(kinds) != 1:
        raise ValueError("Do not pool fixture and live-model evidence")
    evidence = kinds.pop()
    groups = {}
    for condition in plan["conditions"]:
        group = [r for r in rows if r["condition"] == condition]
        n = len(group)
        infra = sum(any(s["status"] in ("infra_error", "invalid_response", "launch_error", "startup_timeout") for s in r["stages"].values()) for r in group)
        metrics = {"planned_trials": n, "infrastructure_invalid_trials": infra,
                   "initial_passed": sum(r["initial_success"] for r in group),
                   "local_chains_passed": sum(r["local_success"] for r in group),
                   "extension_chains_passed": sum(r["extension_success"] for r in group),
                   "both_branches_passed": sum(r["joint_success"] for r in group)}
        from collections import Counter
        metrics["stage_status_counts"]=dict(Counter(s["status"] for r in group for s in r["stages"].values()))
        metrics["budget_excess_tokens_known"]=sum(s.get("budget_excess_tokens") or 0 for r in group for s in r["stages"].values())
        for source,target in (("initial_passed","initial_success_rate"),
                              ("local_chains_passed","local_success_rate"),
                              ("extension_chains_passed","extension_success_rate"),
                              ("both_branches_passed","joint_success_rate")):
            metrics[target] = metrics[source]/n if not infra else None
        all_stages = [s for r in group for s in r["stages"].values() if s["invoked"]]
        token_values = [total_tokens(s["usage"]) for s in all_stages]
        known = all(x is not None for x in token_values) and evidence == "live_model"
        metrics["token_coverage"] = sum(x is not None for x in token_values)/len(token_values) if token_values else 0
        metrics["total_experiment_tokens"] = sum(token_values) if known else None
        metrics["generation_wall_seconds"] = sum(s["generation_wall_seconds"] for s in all_stages)
        metrics["skill_load_events"] = sum(len(s.get("metadata",{}).get("skill_events",[])) for s in all_stages)
        lifecycle = []
        for row in group:
            costs = {stage: total_tokens(s["usage"]) if s["invoked"] else 0 for stage,s in row["stages"].items()}
            lifecycle.append(costs["initial"] + .5*costs["local"] + .5*costs["extension"] if known else None)
        expected_successes = .5*(metrics["local_chains_passed"] + metrics["extension_chains_passed"])
        metrics["weighted_lifecycle_tokens_all_attempts"] = sum(lifecycle) if known else None
        metrics["tokens_per_successful_weighted_chain"] = (
            sum(lifecycle)/expected_successes if known and expected_successes and not infra else None)
        groups[condition] = metrics
    inference = []
    if evidence == "live_model" and not any(g["infrastructure_invalid_trials"] for g in groups.values()):
        strata=sorted({(plan["task_metadata"][r["task"]]["track"],plan["task_metadata"][r["task"]]["language"]) for r in rows})
        for track,language in strata:
            subset=[r for r in rows if plan["task_metadata"][r["task"]]["track"]==track and plan["task_metadata"][r["task"]]["language"]==language]
            for a,b in (("A","B"),("B","C")):
                if a in groups and b in groups:
                    for field in ("initial_success","local_success","extension_success"):
                        inference.append({"track":track,"language":language,**paired_bootstrap(subset,a,b,field)})
    from .fairness import descriptive_strata, sensitivity_diagnostics
    return {"schema_version":1,"evidence_kind":evidence,"groups":groups,"comparisons":inference,
            "strata":descriptive_strata(plan,rows),"sensitivity":sensitivity_diagnostics(plan,rows),
            "model_ranking_supported":False,
            "warning":("Fixture playback validates the evaluator only; no model/Skill comparison is supported."
                       if evidence != "live_model" else
                       "Exploratory microbenchmark; success and cost estimates are not a universal architecture ranking."),
            "cost_note":"Cached/reasoning tokens are subsets, not added again. Missing tokens are null, never zero."}


def write_report(out: Path, summary: dict) -> None:
    write_json(out / "summary.json", summary)
    lines=["# ChangeBench run report", "", f"Evidence: **{summary['evidence_kind']}**", "",summary["warning"], "",
           "| Arm | Planned | Initial pass | Local chain | Extension chain | Infra invalid | Tokens |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for arm,m in summary["groups"].items():
        lines.append(f"| {arm} | {m['planned_trials']} | {m['initial_passed']} | {m['local_chains_passed']} | "
                     f"{m['extension_chains_passed']} | {m['infrastructure_invalid_trials']} | "
                     f"{m['total_experiment_tokens'] if m['total_experiment_tokens'] is not None else 'not measured'} |")
    if summary.get("strata"):
        lines += ["", "## Language/level strata (no cross-language ranking)", "",
                  "| Track | Language | Level | Arm | Trials | Initial | Both branches |",
                  "|---|---|---:|---|---:|---:|---:|"]
        for r in summary["strata"]:
            lines.append(f"| {r['track']} | {r['language']} | {r['level']} | {r['condition']} | {r['planned_trials']} | {r['initial_success_rate']} | {r['joint_success_rate']} |")
    lines += ["",summary["cost_note"],"", "No model ranking or architecture score is inferred from these results.",
              "Mutation controls and evaluator integrity tests are reported separately in validation.json."]
    (out/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def blind_export(run: Path, out: Path, seed: int = 37) -> dict:
    if out.exists() and any(out.iterdir()):
        raise ValueError("Blind export destination must be empty")
    snapshots = list((run/"snapshots").glob("*/*.json"))
    random.Random(seed).shuffle(snapshots)
    out.mkdir(parents=True,exist_ok=True)
    mapping = {}
    with (out/"review.csv").open("w",newline="",encoding="utf-8") as f:
        writer=csv.writer(f)
        writer.writerow(["sample","unnecessary_complexity_evidence","responsibility_leak_evidence",
                         "needed_change_vs_unrelated_change","verdict_or_uncertain"])
        for i,path in enumerate(snapshots):
            sample=f"sample-{i+1:03}"
            files=sanitized(json.loads(path.read_text()))
            materialize(files,out/sample/"project")
            task=path.parent.name.rsplit("-",2)[0]
            (out/sample/"REQUIREMENTS.md").write_text(public_spec(task,path.stem),encoding="utf-8")
            mapping[sample]={"trial_id":path.parent.name,"stage":path.stem,
                             "sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
            writer.writerow([sample,"","","",""])
    lookup={(v["trial_id"],v["stage"]):sample for sample,v in mapping.items()}
    plan=json.loads((run/"plan.json").read_text())
    rng=random.Random(seed+1)
    with (out/"pairs.csv").open("w",newline="",encoding="utf-8") as f:
        writer=csv.writer(f)
        writer.writerow(["left","right","preferred_or_tie_or_uncertain","code_evidence"])
        for task in plan["task_ids"]:
            for repeat in range(plan["repeats"]):
                for stage in ("initial","local","extension"):
                    for a,b in (("A","B"),("B","C")):
                        left=lookup.get((f"{task}-{repeat}-{a}",stage))
                        right=lookup.get((f"{task}-{repeat}-{b}",stage))
                        if left and right:
                            pair=[left,right];rng.shuffle(pair);writer.writerow([*pair,"",""])
    # Keep the treatment key with the controller, outside the reviewer package.
    write_json(run/"blind-key.json",mapping)
    (out/"REVIEW.md").write_text("""# Blind review
Inspect observable behavior and code, not file counts or named design patterns.
For each claim cite file:line and a concrete current responsibility or change.
Mark uncertain rather than guessing. No penalty for interfaces or flat files by themselves.
A legitimate schema change can span several files. Preserve this distinction.
The treatment key is intentionally outside this directory. Code style may still reveal
hints, so this is blinded labeling, not a guarantee against all inference.
""")
    return {"samples":len(mapping),"mapping_location":str(run/"blind-key.json")}
