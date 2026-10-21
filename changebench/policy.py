"""One canonical policy: B and C load exactly the same design body."""
from __future__ import annotations

import hashlib

BASE = """Implement the supplied public requirements. Keep existing documented behavior.
You may choose and change internal structure. Do not modify the evaluation protocol.
Add and run relevant public tests. Report only checks actually executed.
Use the task-declared language and toolchain; standard libraries only for this pilot.
Do not assume access to future requirements, evaluator files, or external services.
"""
DESIGN = """# Design judgment
Keep related behavior cohesive and independently changing responsibilities separable.
Add a boundary when it solves a current correctness, testing, or change-isolation problem.
Do not add layers, interfaces, or dependencies solely for hypothetical future flexibility.
Consider a simpler direct implementation, but do not trade away required invariants.
Keep unrelated behavior stable. Internal refactoring is allowed when it helps the change.
No required architecture, layer count, file count, or design document.
"""
DESCRIPTION = ("Review design tradeoffs when a change alters ownership across responsibilities, "
               "introduces a hard-to-reverse contract, or changes a correctness boundary. "
               "Not needed for ordinary local edits.")
SKILL_PATH = ".agents/skills/design-check/SKILL.md"
SKILL = "---\nname: design-check\ndescription: " + DESCRIPTION + "\n---\n\n" + DESIGN


def policy_hash() -> str:
    return hashlib.sha256(DESIGN.encode()).hexdigest()


def condition_payload(condition: str, maintenance: bool = False) -> dict:
    if condition not in {"A", "B", "C"}:
        raise ValueError("Unknown condition")
    if maintenance:
        return {"instructions": BASE, "skill_catalog": [], "skill_files": {}}
    if condition == "B":
        return {"instructions": BASE + "\n" + DESIGN,
                "skill_catalog": [], "skill_files": {}}
    if condition == "C":
        return {"instructions": BASE,
                "skill_catalog": [{"name": "design-check", "description": DESCRIPTION,
                                   "path": SKILL_PATH}],
                "skill_files": {SKILL_PATH: SKILL}}
    return {"instructions": BASE, "skill_catalog": [], "skill_files": {}}
