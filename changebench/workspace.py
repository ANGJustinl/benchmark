"""Snapshot controls. Role boundaries are enforced by rebuilding clean trees."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

MAX_FILES = 200
MAX_FILE_BYTES = 256_000
MAX_TOTAL_BYTES = 2_000_000
CONTROL_PARTS = {".agents", ".claude", ".codex", ".git", "__pycache__", ".pytest_cache"}
CONTROL_NAMES = {"AGENTS.md", "CLAUDE.md", "SKILL.md", "TASK.md", "REQUEST.json"}


def validate_files(files: dict[str, str]) -> dict[str, str]:
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise ValueError("Snapshot must contain 1..200 files")
    total = 0
    normalized = {}
    for name, text in files.items():
        if not isinstance(name, str) or not isinstance(text, str):
            raise ValueError("Snapshot paths and contents must be strings")
        p = PurePosixPath(name)
        if (not name or not p.parts or any(len(x.encode("utf-8")) > 255 for x in p.parts) or p.is_absolute() or ".." in p.parts or "\\" in name
                or str(p) != name or ":" in name or any(ord(c) < 32 for c in name)):
            raise ValueError("Unsafe or noncanonical snapshot path")
        n = len(text.encode("utf-8"))
        if n > MAX_FILE_BYTES:
            raise ValueError("File size limit exceeded")
        total += n
        normalized[name] = text
    if total > MAX_TOTAL_BYTES:
        raise ValueError("Snapshot total size limit exceeded")
    for name in normalized:
        if any(str(parent) in normalized for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise ValueError("File/directory collision")
    return normalized


def sanitized(files: dict[str, str]) -> dict[str, str]:
    files = validate_files(files)
    return {name: text for name, text in files.items()
            if not (set(PurePosixPath(name).parts) & CONTROL_PARTS)
            and PurePosixPath(name).name not in CONTROL_NAMES
            and not name.endswith(".pyc")}


def materialize(files: dict[str, str], root: Path) -> None:
    files = validate_files(files)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("Symlink root forbidden")
    for name, text in files.items():
        target = root / name
        if any(parent.is_symlink() for parent in (target, *target.parents)):
            raise ValueError("Symlinks forbidden")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def digest(files: dict[str, str]) -> str:
    raw = json.dumps(validate_files(files), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def diff_diagnostics(before: dict[str, str], after: dict[str, str]) -> dict:
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    return {"changed_files": changed, "changed_file_count": len(changed),
            "python_files": sum(k.endswith(".py") for k in after),
            "python_lines": sum(len(v.splitlines()) for k, v in after.items() if k.endswith(".py")),
            "source_files_by_language": {lang:sum(k.endswith(ext) for k in after) for lang,ext in
                (("python",".py"),("typescript",".ts"),("go",".go"))},
            "source_lines_by_language": {lang:sum(len(v.splitlines()) for k,v in after.items() if k.endswith(ext))
                for lang,ext in (("python",".py"),("typescript",".ts"),("go",".go"))},
            "note": "Diagnostics only. Fewer files or lines is not an architecture score."}
