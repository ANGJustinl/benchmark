"""Bounded candidate execution; expected answers stay in the controller.

The local backend is intentionally NOT a security boundary. Use only with trusted
fixtures. Docker is the default for submitted code and has no local fallback.
"""
from __future__ import annotations

import json
import math
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .catalog import cases
from .workspace import materialize, sanitized


@dataclass
class ProcessResult:
    status: str
    returncode: int | None
    stdout: str
    stderr: str
    wall_seconds: float
    startup_seconds: float | None = None
    execution_seconds: float | None = None
    ready: bool | None = None
    cleanup_seconds: float = 0.0
    cleanup_error: str | None = None


def clean_env(home: str) -> dict[str, str]:
    return {"PATH": os.defpath, "HOME": home, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def _limits() -> None:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024**2, 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


DOCKER_READY = b"CHANGEBENCH_READY_V1\n"


def docker_binary() -> str:
    """Resolve once per operation, before entering the sanitized worker environment."""
    configured = os.getenv("CHANGEBENCH_DOCKER_EXECUTABLE")
    value = configured or shutil.which("docker")
    if not value or not os.path.isabs(value) or (configured and not os.access(value, os.X_OK)):
        raise RuntimeError("Docker executable unavailable; no host fallback")
    return str(Path(value).absolute())


def docker_entrypoint(argv: list[str]) -> list[str]:
    # This trusted wrapper emits readiness before any candidate instruction runs.
    # The controller acknowledges it before supplying candidate stdin.
    return ["-c", "printf 'CHANGEBENCH_READY_V1\\n'; IFS= read -r cb_go || exit 125; exec \"$@\"",
            "changebench-init", *argv]


def run_bounded(argv: list[str], data: bytes, cwd: Path, timeout: float = 5,
                output_limit: int = 256_000, env: dict | None = None,
                resource_limits: bool = False, ready_prefix: bytes | None = None,
                startup_timeout: float = 30) -> ProcessResult:
    """Bound startup separately from execution using a trusted, one-shot handshake.

    Both pipes are drained; output, stdin backpressure and process descendants are
    bounded. The candidate cannot reset the deadline by printing another marker.
    """
    if any(not math.isfinite(v) or v <= 0 for v in (timeout, startup_timeout, output_limit)):
        raise ValueError("Positive finite execution limits required")
    if not argv or not all(isinstance(x, str) for x in argv):
        raise ValueError("argv must be a nonempty list of strings")
    if os.name != "posix":
        raise RuntimeError("This runner requires POSIX process groups")
    start = time.monotonic()
    execution_start = None if ready_prefix else start
    ready = None if ready_prefix is None else False
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=clean_env(str(cwd)) if env is None else env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True,
                                preexec_fn=_limits if resource_limits else None)
    except OSError as exc:
        return ProcessResult("launch_error", None, "", str(exc), time.monotonic()-start, ready=ready)
    selector = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    status, offset, total = "ok", 0, 0
    pending_ready = bytearray()
    wire = (b"\n" + data) if ready_prefix else data
    for pipe, label in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
        os.set_blocking(pipe.fileno(), False)
        selector.register(pipe, selectors.EVENT_READ, label)
    os.set_blocking(proc.stdin.fileno(), False)

    def enable_input():
        if wire:
            selector.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            proc.stdin.close()

    if not ready_prefix:
        enable_input()

    def remaining():
        if execution_start is None:
            return startup_timeout - (time.monotonic() - start)
        return timeout - (time.monotonic() - execution_start)

    try:
        while selector.get_map():
            left = remaining()
            if left <= 0:
                status = "startup_timeout" if execution_start is None else "timeout"
                break
            for key, _ in selector.select(min(left, 0.1)):
                pipe, label = key.fileobj, key.data
                if label == "stdin":
                    try:
                        offset += os.write(pipe.fileno(), wire[offset:offset+8192])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        offset = len(wire)
                    if offset >= len(wire):
                        selector.unregister(pipe); pipe.close()
                    continue
                try:
                    chunk = os.read(pipe.fileno(), 8192)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(pipe); pipe.close()
                    continue
                if label == "stdout" and execution_start is None:
                    pending_ready.extend(chunk)
                    if not ready_prefix.startswith(pending_ready[:len(ready_prefix)]):
                        status = "infra_error"
                        output[label].extend(pending_ready[:output_limit])
                        break
                    if len(pending_ready) < len(ready_prefix):
                        continue
                    chunk = bytes(pending_ready[len(ready_prefix):])
                    pending_ready.clear()
                    execution_start = time.monotonic(); ready = True
                    enable_input()
                room = max(0, output_limit-total)
                output[label].extend(chunk[:room]); total += len(chunk)
                if total > output_limit:
                    status = "output_limit"; break
            if status != "ok":
                break
        if status == "ok":
            try:
                proc.wait(timeout=max(0.001, remaining()))
            except subprocess.TimeoutExpired:
                status = "startup_timeout" if execution_start is None else "timeout"
    finally:
        _kill_group(proc)
        proc.wait(timeout=3)
        for key in list(selector.get_map().values()):
            key.fileobj.close()
        if not proc.stdin.closed: proc.stdin.close()
        selector.close()
    if ready_prefix is not None and not ready:
        if status != "startup_timeout": status = "infra_error"
    elif status == "ok" and proc.returncode != 0:
        status = "runtime_error"
    end = time.monotonic()
    return ProcessResult(status, proc.returncode,
                         output["stdout"].decode("utf-8", errors="replace"),
                         output["stderr"].decode("utf-8", errors="replace"), end-start,
                         (execution_start or end)-start if ready_prefix else 0.0,
                         end-execution_start if execution_start is not None else None, ready)


def remove_container(name: str, result: ProcessResult) -> None:
    start = time.monotonic()
    try:
        removed = subprocess.run([docker_binary(), "rm", "-f", name],
                                 capture_output=True, text=True, timeout=10)
        # --rm already removed a successful container. Other failures are visible.
        if removed.returncode and "No such container" not in (removed.stderr or ""):
            result.cleanup_error = "Container cleanup failed"
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        result.cleanup_error = type(exc).__name__
    result.cleanup_seconds = time.monotonic()-start
    if result.cleanup_error:
        result.status = "infra_error"


def strict_json(raw: str):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError("Duplicate JSON key")
            result[k] = v
        return result
    def invalid_number(value):
        raise ValueError("Non-finite JSON value")
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite JSON value")
        return number
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_number, parse_float=finite_float)


def equal_json(actual, expected) -> bool:
    # bool is not an int here; integer contracts require integer JSON outputs.
    return json.dumps(actual, sort_keys=True, ensure_ascii=True, allow_nan=False) == json.dumps(
        expected, sort_keys=True, ensure_ascii=True, allow_nan=False)


def docker_argv(work: Path, image: str, name: str) -> list[str]:
    return [docker_binary(), "run", "--entrypoint=/bin/sh", "--name", name, "--network=none", "--read-only",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=512m", "--cpus=1", "--user=65534:65534", "--tmpfs=/tmp:rw,size=64m",
            "--mount", f"type=bind,src={work.resolve()},dst=/workspace,readonly",
            "--workdir=/workspace", "--interactive", image, *docker_entrypoint(["python", "-I", "-S", "-B", "-c",
            'import runpy,sys;sys.path.insert(0,"/workspace");runpy.run_path("/workspace/main.py",run_name="__main__")'])]



def resolve_docker_image(image: str) -> str:
    """Fail before any paid model call if Docker/image is unavailable; pin image ID."""
    if not docker_binary():
        raise RuntimeError("Docker unavailable")
    try:
        info = subprocess.run([docker_binary(), "info", "--format", "{{.ServerVersion}}"],
                              capture_output=True, text=True, timeout=10)
        if info.returncode != 0:
            raise RuntimeError("Docker daemon is not ready; no model call was started")
        inspected = subprocess.run([docker_binary(), "image", "inspect", image, "--format", "{{.Id}}"],
                                   capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Docker preflight failed") from exc
    value = inspected.stdout.strip()
    if inspected.returncode != 0 or not value.startswith("sha256:"):
        raise RuntimeError("Docker image must be pulled or built before starting the experiment")
    return value


def evaluate(files: dict[str, str], task: str, stage: str, seed: int = 101,
             backend: str = "docker", timeout: float = 5, random_count: int = 18,
             image: str = "python:3.13-slim", trusted_local: bool = False,
             compile_timeout: float = 120, startup_timeout: float = 30) -> dict:
    from .poly_catalog import TASKS as POLY
    if task in POLY:
        from .poly_execution import evaluate as evaluate_poly
        return evaluate_poly(files, task, stage, seed, backend, trusted_local, image,
                             random_count=random_count, timeout=timeout,
                             compile_timeout=compile_timeout, startup_timeout=startup_timeout)
    if backend not in {"local", "docker"}:
        raise ValueError("Unknown backend")
    if backend == "local" and not trusted_local:
        raise ValueError("Local execution requires explicit trusted_local=True")
    if backend == "docker" and not docker_binary():
        raise RuntimeError("Docker unavailable. No unsafe fallback. Local is for trusted fixtures only.")
    suite = cases(task, stage, seed, random_count)
    base = {"task": task, "stage": stage, "backend": backend, "seed": seed,
            "random_count": random_count, "total": len(suite), "passed": 0, "success": False}
    try:
        files = sanitized(files)
        if "main.py" not in files:
            raise ValueError("main.py missing")
    except (TypeError, ValueError) as exc:
        return {**base, "status": "invalid_submission", "error": str(exc), "cases": []}
    payload = "".join(json.dumps(c.input, ensure_ascii=True, allow_nan=False)+"\n" for c in suite).encode()
    with tempfile.TemporaryDirectory(prefix="changebench-candidate-") as temp:
        root = Path(temp)
        materialize(files, root)
        # Docker's unprivileged user must be able to traverse/read the snapshot.
        root.chmod(0o755)
        for path in root.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        name = "changebench-" + uuid.uuid4().hex
        if backend == "local":
            argv = [sys.executable, "-I", "-S", "-B", "-c",
                    'import runpy,sys;sys.path.insert(0,' + repr(str(root)) + ');runpy.run_path("main.py",run_name="__main__")']
        else:
            argv = docker_argv(root, image, name)
        result = ProcessResult("infra_error", None, "", "Execution did not start", 0)
        try:
            result = run_bounded(argv, payload, root, timeout, resource_limits=backend == "local",
                                 ready_prefix=DOCKER_READY if backend == "docker" else None,
                                 startup_timeout=startup_timeout)
        finally:
            if backend == "docker":
                remove_container(name, result)
    lines = result.stdout.splitlines()
    protocol_ok = len(lines) == len(suite)
    details = []
    for i, case in enumerate(suite):
        error = None
        actual = None
        try:
            actual = strict_json(lines[i])
            passed = equal_json(actual, case.expected)
        except (IndexError, ValueError, TypeError) as exc:
            passed, error = False, type(exc).__name__
            protocol_ok = False
        details.append({"id": case.id, "origin": case.origin, "passed": passed,
                        "error": error, "actual": actual if not passed else None})
    count = sum(item["passed"] for item in details)
    success = result.status == "ok" and protocol_ok and count == len(suite)
    status = ("passed" if success else "failed") if result.status == "ok" else result.status
    if result.status == "ok" and not protocol_ok:
        status = "protocol_error"
    return {**base, "passed": count, "success": success, "status": status,
            "protocol_ok": protocol_ok, "cases": details,
            "execution": {**asdict(result), "stdout": None},
            "regression_passed": sum(c["passed"] for c in details if c["origin"] == "initial"),
            "regression_total": sum(c["origin"] == "initial" for c in details),
            "new_passed": sum(c["passed"] for c in details if c["origin"] != "initial"),
            "new_total": sum(c["origin"] != "initial" for c in details)}


def cleanup_worker_containers(session: str) -> str | None:
    """Controller-side cleanup also runs when a model worker is forcibly killed."""
    import re
    if not re.fullmatch(r"[0-9a-f]{32}", session):
        raise ValueError("Invalid cleanup session identifier")
    try:
        listed = subprocess.run([docker_binary(), "ps", "-aq", "--filter", f"label=changebench.session={session}"],
                                capture_output=True, text=True, timeout=5)
        if listed.returncode != 0:
            return "Could not enumerate worker containers"
        ids = listed.stdout.split()
        if any(not re.fullmatch(r"[0-9a-f]{12,64}", value) for value in ids):
            return "Unexpected Docker container identifier"
        if ids:
            removed = subprocess.run([docker_binary(), "rm", "-f", *ids], capture_output=True, timeout=5)
            if removed.returncode != 0:
                return "Could not remove all worker containers"
    except (OSError, subprocess.TimeoutExpired):
        return "Worker container cleanup failed"
    return None
