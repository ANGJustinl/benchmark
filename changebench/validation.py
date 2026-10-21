"""Acceptance tests for the benchmark itself, distinct from model evaluation."""
from __future__ import annotations

from .catalog import STAGES, TASKS
from .execution import evaluate
from .fixtures import reference_files


def validate_harness() -> dict:
    outcomes = []
    def record(name, passed, **details):
        outcomes.append({"check":name,"passed":bool(passed),**details})
    for task in TASKS:
        for stage in STAGES:
            for layout in ("flat","modular"):
                result=evaluate(reference_files(task,stage,layout),task,stage,
                                backend="local",trusted_local=True)
                record(f"valid/{task}/{stage}/{layout}",result["success"],
                       cases=result["total"],accepted=result["passed"])
        result=evaluate(reference_files(task,"initial",mutant=True),task,"initial",
                        backend="local",trusted_local=True)
        record(f"mutation/{task}",not result["success"],rejected=result["total"]-result["passed"])
        for stage in ("local","extension"):
            result=evaluate(reference_files(task,"initial"),task,stage,backend="local",trusted_local=True)
            record(f"no-change/{task}/{stage}",not result["success"] and result["regression_passed"]==result["regression_total"],
                   new_rejected=result["new_total"]-result["new_passed"])
            result=evaluate(reference_files(task,stage,mutant=True),task,stage,backend="local",trusted_local=True)
            record(f"regression/{task}/{stage}",not result["success"] and result["regression_passed"]<result["regression_total"],
                   old_rejected=result["regression_total"]-result["regression_passed"])
    controls={
        "self-reported-pass":('import sys\nfor x in sys.stdin: print("{\\"passed\\":true}")\n',"failed"),
        "empty-success-exit":('pass\n',"protocol_error"),
        "malformed-json":('import sys\nfor x in sys.stdin: print("NOT JSON")\n',"protocol_error"),
        "duplicate-keys":('import sys\nfor x in sys.stdin: print("{\\"host\\":1,\\"host\\":2}")\n',"protocol_error"),
        "timeout":('while True: pass\n',"timeout"),
        "output-flood":('import sys\nwhile True: sys.stdout.write("x"*8192);sys.stdout.flush()\n',"output_limit"),
        "crash":('raise RuntimeError("crash")\n',"runtime_error"),
    }
    for name,(code,expected) in controls.items():
        result=evaluate({"main.py":code},"config","initial",backend="local",trusted_local=True,
                        timeout=.5 if name=="timeout" else 3)
        record(f"integrity/{name}",not result["success"] and result["status"]==expected,
               expected_status=expected,actual_status=result["status"])
    return {"evidence_kind":"harness_validation","checks":len(outcomes),
            "passed":sum(x["passed"] for x in outcomes),
            "failed":sum(not x["passed"] for x in outcomes),"outcomes":outcomes,
            "limitations":["Only trusted local execution was exercised here.",
                           "Mutation controls are hand-selected, not exhaustive mutation coverage.",
                           "These are microprojects, not evidence of large-repository architecture validity."]}
