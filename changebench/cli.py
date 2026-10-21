"""Small CLI: plan, run, evaluate, summarize, and validate."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .catalog import STAGES, TASKS, initial_files, public_spec
from .registry import ALL_TASKS, select_tasks
from .runtimes import runtime_manifest
from .design import make_plan
from .execution import evaluate
from .experiment import CommandAdapter, ReplayAdapter, run_experiment, write_json
from .policy import condition_payload
from .reporting import blind_export, summarize, write_report
from .validation import validate_harness
from .workspace import materialize


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_adapter(config,receipts):
    if config.get("transport")=="codex-cli-app-server":
        from .codex_adapter import CodexAdapter
        adapter=CodexAdapter(receipts)
        if config["model_id"]!=adapter.model_id:raise ValueError("Unsupported Codex model profile")
        return adapter
    return CommandAdapter(config)


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(prog="changebench")
    sub=parser.add_subparsers(dest="command",required=True)
    doctor=sub.add_parser("doctor",help="Check prerequisites without exposing credentials")
    doctor.add_argument("--live",action="store_true")
    doctor.add_argument("--config",type=Path,help="Check credential names for the selected adapter")
    plan=sub.add_parser("plan")
    plan.add_argument("--repeats",type=int,default=3)
    plan.add_argument("--seed",type=int,default=20261015)
    plan.add_argument("--out",type=Path,required=True)
    plan.add_argument("--suite",choices=("legacy","poly","all"),default="poly")
    plan.add_argument("--languages",nargs="+",choices=("python","typescript","go"))
    plan.add_argument("--levels",nargs="+",type=int,choices=(1,2,3,4))
    plan.add_argument("--budget-config",type=Path,help="JSON: profile, budgets_by_level, optional client_profile/evaluation")
    plan.add_argument("--persistent-upgrade",action="store_true",help="Check maintenance against frozen candidate T0 disk (new contract)")
    calibration=sub.add_parser("calibrate",help="Real local fixture calibration; never calls a model")
    calibration.add_argument("--out",type=Path,required=True)
    calibration.add_argument("--repeats",type=int,default=3)
    replay=sub.add_parser("replay",help="Run multilingual A/B/C reference playback, not a model experiment")
    replay.add_argument("--out",type=Path,required=True)
    replay.add_argument("--suite",choices=("legacy","poly","all"),default="poly")
    replay.add_argument("--repeats",type=int,default=1)
    audit=sub.add_parser("audit-run",help="Verify frozen allocation, sealed results and per-case evidence")
    audit.add_argument("run",type=Path)
    selftest=sub.add_parser("selftest",help="Harness validation plus 36 fixture-playback trajectories; no API usage")
    selftest.add_argument("--out",type=Path,required=True)
    selftest.add_argument("--repeats",type=int,default=2)
    run=sub.add_parser("run",help="Live experiment using configured trusted model workers")
    run.add_argument("--plan",type=Path,required=True)
    run.add_argument("--config",type=Path,required=True)
    run.add_argument("--out",type=Path,required=True)
    evaluate_p=sub.add_parser("evaluate")
    evaluate_p.add_argument("--snapshot",type=Path,required=True)
    evaluate_p.add_argument("--task",choices=ALL_TASKS,required=True)
    evaluate_p.add_argument("--stage",choices=STAGES,required=True)
    evaluate_p.add_argument("--seed",type=int,default=101)
    evaluate_p.add_argument("--backend",choices=("docker","local"),default="docker")
    evaluate_p.add_argument("--trusted-local",action="store_true")
    evaluate_p.add_argument("--out",type=Path,required=True)
    evaluate_p.add_argument("--image",help="Locally available runtime image, resolved to immutable ID")
    summary=sub.add_parser("summarize")
    summary.add_argument("run",type=Path)
    blind=sub.add_parser("blind")
    blind.add_argument("run",type=Path)
    blind.add_argument("--out",type=Path,required=True)
    export=sub.add_parser("export-task")
    export.add_argument("--task",choices=ALL_TASKS,required=True)
    export.add_argument("--condition",choices=("A","B","C"),default="A")
    export.add_argument("--out",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=="doctor":
            state={"python":sys.version.split()[0],"python_ok":sys.version_info>=(3,11),
                   "docker_available":bool(shutil.which("docker")),
                   "openai_api_key_configured":bool(os.getenv("OPENAI_API_KEY")),
                   "trusted_selftest_available":os.name=="posix", "poly_runtimes":runtime_manifest()}
            credential_name="OPENAI_API_KEY"
            if args.config:
                selected=read_json(args.config)
                names={"APIZN_API_KEY" if selected[role].get("transport")=="codex-cli-app-server" else "OPENAI_API_KEY" for role in ("builder","maintainer")}
            else:names={credential_name}
            state["credential_presence"]={name:bool(os.getenv(name)) for name in sorted(names)}
            state["live_ready"]=state["docker_available"] and all(state["credential_presence"].values()) and state["python_ok"]
            if args.live and state["live_ready"]:
                from .execution import docker_binary
                checked=__import__('subprocess').run([docker_binary(),"info","--format","{{.ServerVersion}}"],capture_output=True,text=True,timeout=10)
                state["docker_daemon_ready"]=checked.returncode==0
                state["live_ready"]=state["live_ready"] and state["docker_daemon_ready"]
            print(json.dumps(state,indent=2))
            return 0 if not args.live or state["live_ready"] else 1
        if args.command=="plan":
            options={}
            if args.budget_config:
                cfg=read_json(args.budget_config)
                if set(cfg)-{"profile","budgets_by_level","client_profile","evaluation"}:
                    raise ValueError("Unknown budget configuration fields")
                options={"budgets":cfg["budgets_by_level"],"budget_profile":cfg["profile"],
                         "client_profile":cfg.get("client_profile"),"evaluation":cfg.get("evaluation")}
            write_json(args.out,make_plan(args.repeats,args.seed,tasks=select_tasks(args.suite,args.languages,args.levels),
                                         persistent_upgrade=args.persistent_upgrade,**options));print(args.out);return 0
        if args.command=="calibrate":
            from .poly_validation import calibrate
            result=calibrate(args.out,args.repeats)
            print(json.dumps({k:result[k] for k in ("evaluations","checks","passed","failed","unstable_artifact_stage_pairs","model_api_calls")}));return 0 if result["failed"]==0 else 1
        if args.command=="replay":
            from .fairness import audit_run
            data=make_plan(args.repeats,tasks=select_tasks(args.suite))
            rows=run_experiment(data,ReplayAdapter(),ReplayAdapter(),args.out,backend="local",trusted_local=True)
            write_report(args.out,summarize(data,rows));write_json(args.out/"integrity-audit.json",audit_run(args.out))
            print(json.dumps({"fixture_trajectories":len(rows),"model_api_calls":0,"report":str(args.out/"REPORT.md")}));return 0
        if args.command=="audit-run":
            from .fairness import audit_run
            result=audit_run(args.run);print(json.dumps(result,indent=2));return 0
        if args.command=="selftest":
            validation=validate_harness()
            rows=run_experiment(make_plan(args.repeats),ReplayAdapter(),ReplayAdapter(),args.out,
                                backend="local",trusted_local=True)
            plan_data=read_json(args.out/"plan.json")
            write_report(args.out,summarize(plan_data,rows))
            write_json(args.out/"validation.json",validation)
            print(json.dumps({"harness_checks":validation["checks"],"harness_passed":validation["passed"],
                              "fixture_trajectories":len(rows),"model_runs":0,"report":str(args.out/"REPORT.md")},indent=2))
            return 0 if validation["failed"]==0 else 1
        if args.command=="run":
            config=read_json(args.config)
            for role in ("builder","maintainer"):
                if config[role]["model_id"].startswith("REPLACE_"):
                    raise ValueError("Set pinned model identifiers in the live configuration first")
            data=read_json(args.plan)
            rows=run_experiment(data,build_adapter(config["builder"],args.out.parent/(args.out.name+"-receipts")/"builder"),
                build_adapter(config["maintainer"],args.out.parent/(args.out.name+"-receipts")/"maintainer"),
                args.out,budget=config.get("budget"),image=config.get("evaluation_images",config.get("evaluation_image","python:3.13-slim")))
            write_report(args.out,summarize(data,rows));print(args.out/"REPORT.md");return 0
        if args.command=="evaluate":
            from .execution import resolve_docker_image
            image=resolve_docker_image(args.image or "python:3.13-slim") if args.backend=="docker" else None
            result=evaluate(read_json(args.snapshot),args.task,args.stage,args.seed,
                            backend=args.backend,trusted_local=args.trusted_local,image=image)
            write_json(args.out,result);print(result["status"]);return 0 if result["success"] else 1
        if args.command=="summarize":
            from .fairness import audit_run
            audit_run(args.run)
            rows=[json.loads(line) for line in (args.run/"records.jsonl").read_text().splitlines() if line]
            write_report(args.run,summarize(read_json(args.run/"plan.json"),rows));print(args.run/"REPORT.md");return 0
        if args.command=="blind":
            print(json.dumps(blind_export(args.run,args.out),indent=2));return 0
        if args.command=="export-task":
            if args.out.exists() and any(args.out.iterdir()):raise ValueError("Export directory must be empty")
            policy=condition_payload(args.condition)
            files={**initial_files(args.task),"AGENTS.md":policy["instructions"],
                   "TASK.md":public_spec(args.task,"initial"),**policy["skill_files"]}
            materialize(files,args.out);print(args.out);return 0
    except (ValueError,RuntimeError,OSError,KeyError) as exc:
        print(f"error: {exc}",file=sys.stderr)
        return 2
    return 0
