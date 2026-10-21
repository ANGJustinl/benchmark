"""Optional Responses API worker with identical tools in all experimental arms.

No API request is made during selftest. Candidate Python runs only in Docker.
The trusted worker holds credentials; the model's filesystem/tools never do.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .execution import run_bounded, docker_binary, docker_entrypoint, DOCKER_READY, remove_container, ProcessResult
from .experiment import blank_usage, total_tokens, validate_usage
from .workspace import materialize, sanitized, validate_files

API_URL = "https://api.openai.com/v1/responses"


def function(name: str, description: str, properties: dict) -> dict:
    return {"type":"function","name":name,"description":description,"strict":True,
            "parameters":{"type":"object","properties":properties,
                          "required":list(properties),"additionalProperties":False}}


TOOLS = [
    function("list_files", "List current project files. Files are not loaded automatically.", {}),
    function("read_file", "Read one project file.", {"path":{"type":"string"}}),
    function("write_file", "Create or replace a project file.", {"path":{"type":"string"},"content":{"type":"string"}}),
    function("delete_file", "Delete a project file.", {"path":{"type":"string"}}),
    function("run_python", "Run Python arguments inside an offline disposable container. Example: [\"-m\",\"unittest\",\"discover\"].",
             {"args":{"type":"array","items":{"type":"string"}}}),
    function("read_skill", "Load a skill only when its catalog description matches the task.", {"name":{"type":"string"}}),
]


POLY_TOOLS = [t for t in TOOLS if t["name"] != "run_python"] + [
    function("run_project", "Compile the declared language and run caller-provided JSON-lines input in an offline disposable sandbox. No hidden tests are available.",
             {"stdin":{"type":"string"}}),
    function("test_project", "Compile and run caller-authored native tests: unittest, node --test, or go test. Hidden tests are never exposed.", {})]


def post_response(payload: dict, timeout: float) -> dict:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    request = urllib.request.Request(API_URL, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type":"application/json", "Authorization":"Bearer "+key})
    # No automatic retry: hidden retries change the cost budget and independence.
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(8_000_001)
        if len(raw)>8_000_000:
            raise RuntimeError("API response exceeded worker limit")
        return json.loads(raw)


def execute_python(files: dict, args: list[str], image: str, timeout: float) -> dict:
    if not docker_binary():
        raise RuntimeError("Docker is required for live model tool execution")
    if not isinstance(args,list) or not args or not all(isinstance(x,str) for x in args):
        raise ValueError("Nonempty argv array required")
    if sum(len(x) for x in args)>20000:
        raise ValueError("Command too long")
    with tempfile.TemporaryDirectory(prefix="changebench-tool-") as temp:
        root=Path(temp);materialize(files,root);root.chmod(0o755)
        for p in root.rglob("*"):
            p.chmod(0o755 if p.is_dir() else 0o644)
        name="changebench-tool-"+uuid.uuid4().hex
        argv=[docker_binary(),"run","--entrypoint=/bin/sh","--name",name,"--network=none","--read-only",
              "--cap-drop=ALL","--security-opt=no-new-privileges","--pids-limit=64",
              "--memory=512m","--cpus=1","--user=65534:65534","--tmpfs=/tmp:rw,size=64m",
              "--mount",f"type=bind,src={root},dst=/workspace,readonly","--workdir=/workspace",
              "--interactive",image,*docker_entrypoint(["python","-S","-B",*args])]
        session = os.getenv("CHANGEBENCH_RUN_ID", "")
        if re.fullmatch(r"[0-9a-f]{32}", session):
            argv[2:2] = ["--label", f"changebench.session={session}"]
        result=ProcessResult("infra_error",None,"","Execution did not start",0)
        try:
            result=run_bounded(argv,b"",root,timeout=timeout,output_limit=32000,ready_prefix=DOCKER_READY)
        finally:
            remove_container(name,result)
        return {"status":result.status,"returncode":result.returncode,
                "stdout":result.stdout,"stderr":result.stderr,"wall_seconds":result.wall_seconds}


def checkpoint(request,files):
    if request.get("checkpoint_path"):
        from .experiment import write_json
        write_json(Path(request["checkpoint_path"]),files)


def run_request(request: dict, post=post_response, execute=execute_python) -> dict:
    files=validate_files(request["files"])
    checkpoint(request,files)
    settings=request.get("adapter_settings",{})
    model=request["model_id"]
    budget=request["budget"]
    usage={key:0 for key in blank_usage()}
    skill_events=[];api_calls=[];verification_events=[]
    start=time.monotonic()
    catalog=request.get("skill_catalog",[])
    # Crucially, skill_files and source contents are not dumped into the prompt.
    history=[{"role":"user","content":request["public_task"]+"\n\nAvailable project paths:\n"+
              "\n".join(sorted(files))+"\n\nAvailable skills (load with read_skill):\n"+json.dumps(catalog)}]
    status="budget_exhausted"
    error=None
    known_usage=None
    try:
        while usage["tool_calls"]<budget["tool_calls"] and total_tokens(usage)<budget["total_tokens"]:
            remaining=budget["wall_seconds"]-(time.monotonic()-start)
            if remaining<=1:
                break
            payload={"model":model,"instructions":request["instructions"],"input":history,
                     "tools":POLY_TOOLS if request.get("protocol")=="poly-jsonl" else TOOLS,"store":False,"include":["reasoning.encrypted_content"],
                     "max_output_tokens":min(8000,max(16,budget["total_tokens"]-total_tokens(usage)))}
            if settings.get("reasoning_effort"):
                payload["reasoning"]={"effort":settings["reasoning_effort"]}
            response=post(payload,min(120,remaining))
            u=response.get("usage")
            if not isinstance(u,dict) or type(u.get("input_tokens")) is not int or type(u.get("output_tokens")) is not int:
                raise RuntimeError("API usage missing; refusing fabricated token counts")
            measured=validate_usage({"input_tokens":u["input_tokens"],"output_tokens":u["output_tokens"],
                "cached_input_tokens":u.get("input_tokens_details",{}).get("cached_tokens"),
                "reasoning_tokens":u.get("output_tokens_details",{}).get("reasoning_tokens"),"tool_calls":0})
            for key,value in measured.items():
                if key=="tool_calls":continue
                if value is None or usage[key] is None:usage[key]=None
                else:usage[key]+=value
            api_calls.append({"id":response.get("id"),"resolved_model":response.get("model"),
                              "usage":u,"status":response.get("status")})
            if total_tokens(usage)>budget["total_tokens"]:
                status="budget_exhausted"
                break
            if response.get("model") is not None and response["model"] != model:
                raise RuntimeError("Resolved model differs from pinned requested model")
            output=response.get("output",[])
            # Preserve reasoning items for continuation; do not export them to the audit log.
            history.extend(output)
            calls=[item for item in output if item.get("type")=="function_call"]
            if not calls:
                status="completed" if response.get("status")=="completed" else "budget_exhausted"
                break
            for call in calls:
                if usage["tool_calls"]>=budget["tool_calls"]:
                    break
                usage["tool_calls"]+=1
                try:
                    args=json.loads(call["arguments"])
                    name=call["name"]
                    if name=="list_files":
                        result=sorted(files)
                    elif name=="read_file":
                        result=files[args["path"]]
                    elif name=="write_file":
                        updated=validate_files({**files,args["path"]:args["content"]})
                        if sanitized(updated)!=updated:
                            raise ValueError("Experiment control paths are not editable")
                        files=updated;result="written"
                        checkpoint(request,files)
                    elif name=="delete_file":
                        if args["path"] not in files:raise KeyError("File missing")
                        files=dict(files);del files[args["path"]];result="deleted"
                        checkpoint(request,files)
                    elif name=="read_skill":
                        entry=next((s for s in catalog if s["name"]==args["name"]),None)
                        if not entry:raise ValueError("Skill unavailable")
                        result=request["skill_files"][entry["path"]]
                        skill_events.append({"name":entry["name"],"call_id":call["call_id"]})
                    elif name in ("run_project","test_project") and request.get("protocol")=="poly-jsonl":
                        from .runtimes import execute_public
                        remaining=budget["wall_seconds"]-(time.monotonic()-start)
                        if remaining<=1:raise RuntimeError("Wall budget exhausted")
                        result=execute_public(files,args.get("stdin", ""),request["language"],settings.get("docker_image"),min(120,remaining),action="test" if name=="test_project" else "run")
                        verification_events.append({"language":request["language"],"status":result["status"],
                                                    "returncode":result["returncode"]})
                    elif name=="run_python" and request.get("protocol")!="poly-jsonl":
                        remaining=budget["wall_seconds"]-(time.monotonic()-start)
                        if remaining<=1:raise RuntimeError("Wall budget exhausted")
                        result=execute(files,args["args"],settings.get("docker_image","python:3.13-slim"),min(20,remaining))
                        verification_events.append({"args":args["args"],"status":result["status"],
                                                    "returncode":result["returncode"]})
                    else:
                        raise ValueError("Unknown tool")
                except (ValueError,KeyError,TypeError) as exc:
                    result={"error":str(exc)}
                history.append({"type":"function_call_output","call_id":call["call_id"],"output":json.dumps(result)})
    except (RuntimeError,OSError,urllib.error.URLError) as exc:
        # HTTP/auth failures are infrastructure errors, not model correctness failures.
        status="infra_error";error=str(exc)
        known_usage=dict(usage)
        for key in ("input_tokens","output_tokens","cached_input_tokens","reasoning_tokens"):
            usage[key]=None
    if total_tokens(usage) is not None and total_tokens(usage)>budget["total_tokens"]:
        status="budget_exhausted"
    return {"schema_version":1,"evidence_kind":"live_model","model_id":model,"status":status,
            "files":files,"usage":usage,"known_usage":known_usage or dict(usage),"skill_events":skill_events,"api_calls":api_calls,
            "verification_events":verification_events,"seed_supported":False,"error":error}


def main() -> int:
    try:
        request=json.load(sys.stdin)
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY not configured; no live experiment was run")
        if not docker_binary():
            raise RuntimeError("Docker unavailable; no insecure tool-execution fallback")
        result=run_request(request)
    except Exception as exc:
        result={"schema_version":1,"model_id":locals().get("request",{}).get("model_id","unknown"),
                "evidence_kind":"live_model","status":"infra_error","error":str(exc),
                "files":{},"usage":blank_usage(),"skill_events":[]}
    print(json.dumps(result,allow_nan=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
