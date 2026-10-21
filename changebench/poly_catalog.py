"""Versioned, language-neutral contracts and controller-only oracles.

Difficulty labels are design hypotheses, NOT calibrated model difficulty.
Three ports of one family are correlated observations, not independent tasks.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import dataclass

LANGUAGES = ("python", "typescript", "go")
STAGES = ("initial", "local", "extension")
FAMILIES = {
    "settings": (1, "greenfield", "configuration precedence and validation"),
    "invoice": (1, "greenfield", "exact monetary arithmetic"),
    "events": (2, "brownfield", "aggregation and versioned consumer contracts"),
    "routing": (2, "brownfield", "provider wire contracts and retry policy"),
    "ledger": (3, "brownfield", "idempotent transfers and atomic rollback"),
    "scheduler": (3, "brownfield", "dependency scheduling and failure propagation"),
    "store": (4, "brownfield", "durable versioned storage and legacy migration"),
    "outbox": (4, "brownfield", "durable delivery, leases and stale acknowledgements"),
}
TASKS = tuple(f"{f}-{lang}" for f in FAMILIES for lang in LANGUAGES)
SPECS = {
"settings": {
"initial": """Input {config:object={}}. Return {host,port,debug}, default localhost,8080,false.
Only these three config fields matter. host must be a nonempty string (preserve spaces),
port an integer 1..65535, debug a boolean. Validate final values; otherwise {error:invalid}.""",
"local": """Optional env object: APP_HOST overrides host, APP_PORT is a nonempty ASCII
unsigned digit string, APP_DEBUG is case-insensitive true/false only. Malformed recognized
overrides are invalid. Apply overrides before final validation, ignoring unrelated keys.""",
"extension": """Optional profile string selects config.profiles[profile], which must be an
object. Overlay its known fields on root known fields, then validate. Missing profile or
nonobject selection is invalid. No env support is required in this branch."""},
"invoice": {
"initial": """Input {items:[{price:int>=0,qty:int>0}],bps:int=0}. bps is 0..10000.
Return {subtotal,discount,total}; subtotal=sum(price*qty), discount=floor(subtotal*bps/10000),
total=subtotal-discount. Empty items valid. Invalid specified values -> {error:invalid}.""",
"local": """Optional cap:int>=0 limits discount AFTER rounding. Absent cap has no limit.
Keep all old output keys and behavior.""",
"extension": """Optional tax_bps:int in 0..10000. When present add tax=floor(total*tax_bps/10000)
to output and add that tax to total. When absent the exact original shape must remain.
This branch does not require cap."""},
"events": {
"initial": """Input {records:[{customer:nonempty string,cents:int>=0}]}. Aggregate all records
per customer, in first-appearance order. Return {rows:[{customer,cents}],total:sum(cents)}.
Empty records valid. Invalid records -> {error:invalid}. Customer names are ASCII.""",
"local": """Optional min_cents:int>=0=0 filters AFTER aggregating each customer, not before.
Total includes only retained groups. Preserve group order and original behavior.""",
"extension": """Optional version:int=1; only 1 and 2 valid. Version 1 is exactly the old shape.
Version 2 accepts optional currency USD|EUR per record, default USD; aggregate by
(customer,currency) preserving first pair appearance, return
{schema:2,rows:[{customer,currency,cents}],totals:{USD:sum,EUR:sum}}. Both totals exist.
Currency is irrelevant to v1. No min_cents support is required in this branch."""},
"routing": {
"initial": """Input {to:nonempty string,text:nonempty string,provider:email(default),
responses:[HTTP status int 100..599]=[200]}. responses must be nonempty. Only email
supported. Use FIRST response. Return {provider,attempts:1,status:sent|failed,
request:{to,body:text}}; 2xx sent, otherwise failed. Invalid values -> {error:invalid}.""",
"local": """Optional max_attempts:int 1..5=1. Retry only 429 or 5xx, stop on success or
nonretryable status. Reuse last response if exhausted. attempts counts actual attempts.""",
"extension": """Support provider=sms with request {number:to,text}. SMS text length <=160
Unicode code points, NOT UTF-16 code units or bytes. Email retains unlimited length.
No retry support is required in this branch."""},
"ledger": {
"initial": """Input {balances:{account:nonnegative int},actions:[{op:transfer,id,from,to,amount}]}.
All ids/accounts are nonempty ASCII strings. Accounts must exist; amount is a positive
integer; from != to. Malformed scenario -> {error:invalid}. Each line starts fresh.
A successful transfer debits from and credits to, returning applied. Insufficient funds
returns insufficient, WITHOUT reserving id. A successful id replay with the same tuple
returns duplicate without moving funds; reuse with a different tuple returns conflict.
Return {balances:final copy,results:[status per action]}.""",
"local": """Add {op:reverse,id}. Successful unreversed receipt: undo its transfer only if
receiver currently has enough funds, status reversed. Otherwise insufficient. Already
reversed -> duplicate; unknown id -> missing. Original successful ids remain reserved;
replaying the original transfer after reversal is duplicate, NOT a new transfer.""",
"extension": """Add {op:batch,items:[ordinary transfer actions]}. Execute provisionally in
order; applied and duplicate are success. Commit all on success -> committed. Any
conflict/insufficient rolls back balances AND receipts -> rejected. Malformed items
make the scenario invalid. Empty batch commits. No reverse support required."""},
"scheduler": {
"initial": """Input {jobs:[{id:nonempty ASCII string,deps:[job ids]}]}. Unique ids, no duplicate
deps, unknown deps or cycles; otherwise {error:invalid}. Return {waves:[[ids...],...]}
by repeatedly choosing all not-yet-run jobs whose dependencies finished in EARLIER waves;
each wave sorted by ASCII id. Empty graph has no waves. Do not run newly ready jobs in
the same wave as their prerequisites.""",
"local": """Optional max_parallel:int>0 limits jobs chosen in each wave; pick smallest ids
among READY jobs, not globally. Recompute readiness after each wave. Default unlimited.""",
"extension": """Optional failed:[unique existing ids]=[]. These jobs cannot execute; all
transitive dependants are blocked. Return {waves,failed:sorted direct failed,
blocked:sorted dependants excluding direct failed} only when failed key is present.
Without failed key keep exact old shape. Validate original graph including cycles,
even if failed nodes would hide a cycle. No max_parallel support required."""},
"store": {
"initial": """Implement a persistent key store, one request per line. CB_STATE_DIR is an
existing writable private directory shared across PROCESS RESTARTS. No other external IO.
Requests: {op:put,id,key,value:string} and {op:get,key}. ids/keys nonempty ASCII strings.
get -> {value,version} or {status:missing}. put increments key version from 0; returns
{status:stored,version}. Successful id replay with same key/value returns the ORIGINAL
response (even after later writes); different tuple -> {status:conflict}.
Respond only after saving state. On first use, if legacy.json exists, import format
{format:1,entries:{key:{value:string,version:positive int}},requests:{}}. Do not reimport
legacy data after later writes. New on-disk format is unrestricted. Inputs are well-typed.
Malformed op/id/key -> {error:invalid}; state unchanged. No simultaneous writers required.""",
"local": """put accepts optional expected:int>=0 (0 means absent). Check successful-id replay
FIRST. For a new id, mismatch against current version -> {status:conflict}, do not reserve
id. Matching expectation writes normally. Id identity includes expected presence/value.
Preserve old puts without expected. State and receipts must survive fresh processes.""",
"extension": """Add {op:batch,items:[put requests, optional expected:int>=0]}. All writes and
idempotency receipts commit together; any conflict -> {status:rejected} with no effects.
Success -> {status:committed,versions:[each stored/replayed version]}. Empty batch commits.
Single puts support expected too. Preserve legacy data and receipts across processes.
Atomicity here means rejected batches leave no effects; power-loss/OS crash durability
and simultaneous writers are explicitly outside this contract."""},
"outbox": {
"initial": """Persistent FIFO outbox using CB_STATE_DIR across process restarts. Requests:
{op:enqueue,id,payload:string} -> {status:queued}; same successful id+payload replay ->
{status:duplicate}; different payload -> {status:conflict}. Id receipts never disappear.
{op:take} removes oldest available message -> {id,payload}, or null if empty.
Respond after persisting. First-use legacy.json import: {format:1,items:[{id,payload}]}.
Never reimport consumed messages. ids nonempty ASCII strings. No simultaneous writers.
Unknown op/invalid id -> {error:invalid}, state unchanged.""",
"local": """Add {op:cancel,id}; pending message -> {status:canceled}; consumed/already canceled/
unknown id -> {status:missing}. Receipt still prevents re-enqueue, including after restart.""",
"extension": """Add {op:claim,now:int>=0,lease:int>0}. Pick FIFO pending unleased or expired
(until<=now); return {id,payload,token:positive int,until:now+lease}, or null. Each successful
claim issues a globally increasing token, persisted. Add {op:ack,id,token:int>0}; only
current token of pending message -> {status:acked}; otherwise {status:stale}. Expiry alone
does not invalidate an ack; a later re-claim does. take must ignore all leased items
(including expired: take has no clock). Use supplied time, never wall clock. Preserve old
operations and legacy imports; no cancel support required in this branch."""},
}

RUNTIME_TEXT = {
"python": "Python 3.11+ standard library. Entrypoint main.py; internal modules unrestricted.",
"typescript": "TypeScript ES2022/CommonJS, strict tsc compilation, Node 22+. Entrypoint main.ts. Node standard library only. Provided node-shim.d.ts declares require/process. No npm download.",
"go": "Go 1.23+ standard library. Entrypoint main package in root .go files. GO111MODULE=off, CGO_ENABLED=0. No go module download.",
}


def metadata(task: str) -> dict:
    family, lang = task.rsplit("-", 1)
    if family not in FAMILIES or lang not in LANGUAGES:
        raise ValueError("Unknown poly task")
    level, kind, topic = FAMILIES[family]
    return {"task": task, "family": family, "language": lang, "level": level,
            "kind": kind, "topic": topic, "track": "authored-poly",
            "difficulty_status": "provisional_unmeasured", "persistent": level == 4}


def public_spec(task: str, stage: str) -> str:
    m = metadata(task)
    if stage not in STAGES:
        raise ValueError("Unknown stage")
    return (RUNTIME_TEXT[m["language"]] + "\nRead JSON-lines from stdin and emit exactly one JSON value per line. "
            "Diagnostics on stderr. Object key order is immaterial; array order and output keys are contracts. "
            "Integers exclude booleans. Input integers have absolute value <=1000000 unless a narrower bound is stated; all intermediate arithmetic fits within 2^53-1. Strings contain Unicode scalar values (no unpaired surrogates). "
            "Unless explicitly invalid, inputs have documented types. Unknown optional keys may be ignored. "
            "Do not assume future requirements. No architecture/file-count mandate.\nCURRENT REQUIREMENTS\n"
            + SPECS[m["family"]]["initial"] + ("\nCHANGE REQUEST\n" + SPECS[m["family"]][stage] if stage != "initial" else ""))


def _int(x, lo=0, hi=1_000_000):
    if type(x) is not int or not lo <= x <= hi:
        raise ValueError("integer")
    return x


def _str(x):
    if not isinstance(x, str) or not x:
        raise ValueError("string")
    return x


def reference_state(family: str, legacy=None) -> dict:
    if family == "store":
        return {"entries": copy.deepcopy((legacy or {}).get("entries", {})), "requests": {}}
    if family == "outbox":
        items = (legacy or {}).get("items", [])
        return {"items": [{**v, "done": False, "token": 0, "until": 0} for v in items], "counter": 0}
    return {}


def oracle(family: str, stage: str, data: dict, state=None):
    """Controller implementation. Candidate implementations never import this module."""
    d = copy.deepcopy(data)
    s = copy.deepcopy(state) if state is not None else None
    try:
        result = _oracle(family, stage, d, s)
    except (ValueError, KeyError, TypeError):
        return {"error": "invalid"}, state
    return result, s


def _oracle(f, stage, d, s):
    if f == "settings":
        conf = d.get("config", {})
        out = {"host": "localhost", "port": 8080, "debug": False}
        out.update({k: conf[k] for k in out if k in conf})
        if stage == "extension" and "profile" in d:
            profile = conf.get("profiles", {}).get(d["profile"])
            if not isinstance(profile, dict): raise ValueError()
            out.update({k: profile[k] for k in out if k in profile})
        if stage == "local":
            for k, v in d.get("env", {}).items():
                if k == "APP_HOST": out["host"] = v
                if k == "APP_PORT":
                    if not isinstance(v,str) or not v or any(c not in "0123456789" for c in v): raise ValueError()
                    out["port"] = int(v)
                if k == "APP_DEBUG":
                    if not isinstance(v,str) or v.lower() not in ("true","false"): raise ValueError()
                    out["debug"] = v.lower() == "true"
        _str(out["host"]); _int(out["port"], 1, 65535)
        if type(out["debug"]) is not bool: raise ValueError()
        return out
    if f == "invoice":
        subtotal = sum(_int(x["price"]) * _int(x["qty"], 1) for x in d["items"])
        disc = subtotal * _int(d.get("bps", 0), 0, 10000) // 10000
        if stage == "local" and "cap" in d: disc = min(disc, _int(d["cap"]))
        out = {"subtotal": subtotal, "discount": disc, "total": subtotal-disc}
        if stage == "extension" and "tax_bps" in d:
            out["tax"] = out["total"] * _int(d["tax_bps"], 0, 10000) // 10000
            out["total"] += out["tax"]
        return out
    if f == "events":
        version = d.get("version",1) if stage == "extension" else 1
        if type(version) is not int or version not in (1,2): raise ValueError()
        sums = {}
        for r in d["records"]:
            customer, cents = _str(r["customer"]), _int(r["cents"])
            cur = r.get("currency","USD") if version == 2 else None
            if version == 2 and cur not in ("USD","EUR"): raise ValueError()
            key = customer, cur
            sums[key] = sums.get(key,0) + cents
        minimum = _int(d.get("min_cents",0)) if stage == "local" else 0
        rows = [{"customer": k[0], **({"currency":k[1]} if version==2 else {}), "cents":v}
                for k,v in sums.items() if v >= minimum]
        if version == 1: return {"rows":rows,"total":sum(x["cents"] for x in rows)}
        return {"schema":2,"rows":rows,"totals":{c:sum(x["cents"] for x in rows if x["currency"]==c) for c in ("USD","EUR")}}
    if f == "routing":
        to,text=_str(d["to"]),_str(d["text"]); provider=d.get("provider","email")
        if provider not in (("email","sms") if stage=="extension" else ("email",)): raise ValueError()
        if provider=="sms" and len(text)>160: raise ValueError()
        responses=d.get("responses",[200])
        if not responses: raise ValueError()
        for r in responses:_int(r,100,599)
        maximum=_int(d.get("max_attempts",1),1,5) if stage=="local" else 1
        attempts=0
        while attempts<maximum:
            code=responses[min(attempts,len(responses)-1)]; attempts+=1
            if 200<=code<=299 or not (code==429 or code>=500):break
        return {"provider":provider,"attempts":attempts,"status":"sent" if 200<=code<=299 else "failed",
                "request": {"to":to,"body":text} if provider=="email" else {"number":to,"text":text}}
    if f == "ledger":
        balances={_str(k):_int(v) for k,v in d["balances"].items()}; receipts={}; reversed_ids=set()
        def transfer(a):
            ident,src,dst,amount=_str(a["id"]),_str(a["from"]),_str(a["to"]),_int(a["amount"],1)
            if src==dst or src not in balances or dst not in balances:raise ValueError()
            value=(src,dst,amount)
            if ident in receipts:return "duplicate" if receipts[ident]==value else "conflict"
            if balances[src]<amount:return "insufficient"
            balances[src]-=amount;balances[dst]+=amount;receipts[ident]=value
            return "applied"
        results=[]
        for a in d["actions"]:
            if a["op"]=="transfer":result=transfer(a)
            elif a["op"]=="reverse" and stage=="local":
                ident=_str(a["id"])
                if ident not in receipts:result="missing"
                elif ident in reversed_ids:result="duplicate"
                else:
                    src,dst,amount=receipts[ident]
                    if balances[dst]<amount:result="insufficient"
                    else:balances[dst]-=amount;balances[src]+=amount;reversed_ids.add(ident);result="reversed"
            elif a["op"]=="batch" and stage=="extension":
                old_bal,old_rec=balances.copy(),receipts.copy();good=True
                for item in a["items"]:
                    if item["op"]!="transfer":raise ValueError()
                    if transfer(item) not in ("applied","duplicate"):good=False
                if not good:balances,receipts=old_bal,old_rec
                result="committed" if good else "rejected"
            else:raise ValueError()
            results.append(result)
        return {"balances":balances,"results":results}
    if f == "scheduler":
        graph={}
        for j in d["jobs"]:
            ident=_str(j["id"])
            if ident in graph or len(j["deps"])!=len(set(j["deps"])):raise ValueError()
            graph[ident]=set(j["deps"])
        if any(dep not in graph for deps in graph.values() for dep in deps):raise ValueError()
        done=set()
        while len(done)<len(graph):
            ready={k for k,v in graph.items() if k not in done and v<=done}
            if not ready:raise ValueError()
            done |= ready
        failed=set(d.get("failed",[])) if stage=="extension" else set()
        if not failed<=graph.keys() or stage=="extension" and len(failed)!=len(d.get("failed",[])):raise ValueError()
        blocked=set(failed)
        while True:
            more={k for k,v in graph.items() if v & blocked}
            if more<=blocked:break
            blocked |= more
        cap=_int(d.get("max_parallel",max(1,len(graph))),1) if stage=="local" else max(1,len(graph))
        done=set();waves=[]
        while len(done)+len(blocked)<len(graph):
            ready=sorted(k for k,v in graph.items() if k not in done|blocked and v<=done)[:cap]
            waves.append(ready);done.update(ready)
        out={"waves":waves}
        if stage=="extension" and "failed" in d:out.update(failed=sorted(failed),blocked=sorted(blocked-failed))
        return out
    if f == "store":
        def put(a):
            ident,key=_str(a["id"]),_str(a["key"])
            if not isinstance(a["value"],str):raise ValueError()
            signature={"key":key,"value":a["value"]}
            if stage!="initial" and "expected" in a:signature["expected"]=_int(a["expected"])
            if ident in s["requests"]:
                r=s["requests"][ident]
                return copy.deepcopy(r["response"]) if r["signature"]==signature else {"status":"conflict"}
            version=s["entries"].get(key,{"version":0})["version"]
            if "expected" in signature and signature["expected"]!=version:return {"status":"conflict"}
            response={"status":"stored","version":version+1}
            s["entries"][key]={"value":a["value"],"version":version+1}
            s["requests"][ident]={"signature":signature,"response":response}
            return copy.deepcopy(response)
        if d["op"]=="get":return copy.deepcopy(s["entries"].get(_str(d["key"]),{"status":"missing"}))
        if d["op"]=="put":return put(d)
        if d["op"]=="batch" and stage=="extension":
            before=copy.deepcopy(s);versions=[];good=True
            for a in d["items"]:
                if a["op"]!="put":raise ValueError()
                r=put(a)
                if r["status"]!="stored":good=False
                else:versions.append(r["version"])
            if not good:s.clear();s.update(before);return {"status":"rejected"}
            return {"status":"committed","versions":versions}
        raise ValueError()
    if f == "outbox":
        if d["op"]=="enqueue":
            ident=_str(d["id"]);payload=d["payload"]
            if not isinstance(payload,str):raise ValueError()
            previous=next((v for v in s["items"] if v["id"]==ident),None)
            if previous:return {"status":"duplicate" if previous["payload"]==payload else "conflict"}
            s["items"].append({"id":ident,"payload":payload,"done":False,"token":0,"until":0})
            return {"status":"queued"}
        if d["op"]=="take":
            item=next((v for v in s["items"] if not v["done"] and v["token"]==0),None)
            if not item:return None
            item["done"]=True;return {"id":item["id"],"payload":item["payload"]}
        if d["op"]=="cancel" and stage=="local":
            ident=_str(d["id"]);item=next((v for v in s["items"] if v["id"]==ident and not v["done"]),None)
            if not item:return {"status":"missing"}
            item["done"]=True;return {"status":"canceled"}
        if d["op"]=="claim" and stage=="extension":
            now,lease=_int(d["now"]),_int(d["lease"],1)
            item=next((v for v in s["items"] if not v["done"] and (v["token"]==0 or v["until"]<=now)),None)
            if not item:return None
            s["counter"]+=1;item["token"]=s["counter"];item["until"]=now+lease
            return {k:item[k] for k in ("id","payload","token","until")}
        if d["op"]=="ack" and stage=="extension":
            ident,token=_str(d["id"]),_int(d["token"],1)
            item=next((v for v in s["items"] if v["id"]==ident and not v["done"] and v["token"]==token),None)
            if not item:return {"status":"stale"}
            item["done"]=True;return {"status":"acked"}
        raise ValueError()
    raise ValueError("Unknown family")


@dataclass(frozen=True)
class Scenario:
    id: str
    origin: str
    sessions: list[list[dict]]
    expected: list
    legacy: dict | None = None
    contract: str = ""


def _scenario(family, stage, name, sessions, legacy=None, origin=None):
    state=reference_state(family,legacy); expected=[]
    for session in sessions:
        for request in session:
            result,state=oracle(family,stage,request,state)
            expected.append(result)
    return Scenario(name,origin or stage,copy.deepcopy(sessions),expected,copy.deepcopy(legacy),name)


def suites(task: str, stage: str, seed: int=101, count: int=16) -> list[Scenario]:
    f=metadata(task)["family"]
    if stage not in STAGES or type(count) is not int or count<0:raise ValueError("Invalid suite")
    # Same base cases and expectations in every branch; future feature data is private.
    base=_inputs(f,"initial",seed,count)
    result=[_scenario(f,"initial",f"base-{i}",s,l,origin="initial") for i,(s,l) in enumerate(base)]
    if stage!="initial":
        result += [_scenario(f,stage,f"{stage}-{i}",s,l) for i,(s,l) in enumerate(_inputs(f,stage,seed+7919,count))]
    return result


def _inputs(f,stage,seed,count):
    rng=random.Random(seed); data=[]
    def one(d): data.append(([[d]],None))
    def seq(*sessions,legacy=None):data.append((list(sessions),legacy))
    if f=="settings":
        if stage=="initial":
            for d in ({},{"config":{"host":" x ","port":1}},{"config":{"port":65535,"debug":True}},
                      {"config":{"port":True}},{"config":{"port":0}},{"config":{"debug":"true"}},{"config":{"host":""}}):one(d)
            for _ in range(count):one({"config":{"port":rng.randint(1,65535),"debug":bool(rng.randrange(2))}})
        elif stage=="local":
            for v in ("00091","65536","","-1","1.0",17," 2"):one({"config":{"port":0},"env":{"APP_PORT":v,"APP_HOST":"new","APP_DEBUG":"TrUe"}})
            one({"env":{"APP_DEBUG":"yes"}});one({"env":{"ignored":"x"}})
        else:
            for p in ("prod","missing"):one({"config":{"port":0,"profiles":{"prod":{"port":9,"debug":True}}},"profile":p})
            one({"config":{"profiles":{"prod":9}},"profile":"prod"})
            one({"config":{"profiles":{}},"profile":"__proto__"})
            one({"config":{"profiles":{"__proto__":{"port":99}}},"profile":"__proto__"})
    if f=="invoice":
        if stage=="initial":
            for d in ({"items":[]},{"items":[{"price":1,"qty":1}],"bps":5000},{"items":[{"price":True,"qty":1}]},
                      {"items":[{"price":1,"qty":0}]},{"items":[],"bps":10001}):one(d)
            for _ in range(count):one({"items":[{"price":rng.randrange(10000),"qty":rng.randint(1,30)} for j in range(rng.randrange(6))],"bps":rng.randrange(10001)})
        elif stage=="local":
            for cap in (0,1,1000,-1,True):one({"items":[{"price":199,"qty":3}],"bps":7500,"cap":cap})
        else:
            for tax in (0,1,3333,10000,-1,True):one({"items":[{"price":199,"qty":3}],"bps":2000,"tax_bps":tax})
    if f=="events":
        if stage=="initial":
            one({"records":[]});one({"records":[{"customer":"a","cents":4},{"customer":"a","cents":5}]});one({"records":[{"customer":"a","cents":-1}]})
            one({"records":[{"customer":"a","cents":True}]})
            for _ in range(count):one({"records":[{"customer":rng.choice(["c","a","b","constructor"]),"cents":rng.randrange(100)} for j in range(rng.randrange(15))]})
        elif stage=="local":
            for minimum in (0,3,7,15,-1,True):one({"records":[{"customer":"a","cents":4},{"customer":"b","cents":9},{"customer":"a","cents":4}],"min_cents":minimum})
        else:
            one({"records":[],"version":2});one({"records":[],"version":3})
            one({"records":[{"customer":"a","cents":7,"currency":"EUR"},{"customer":"a","cents":9},{"customer":"a","cents":3,"currency":"EUR"}],"version":2})
            one({"records":[{"customer":"a","cents":3,"currency":"GBP"}],"version":2})
    if f=="routing":
        if stage=="initial":
            for status in (199,200,299,300,429,500,599,99,True):one({"to":"a","text":"x","responses":[status]})
            one({"to":"","text":"x"});one({"to":"a","text":"x","responses":[]})
            for _ in range(count):one({"to":"a","text":"x"*rng.randint(1,200),"responses":[rng.randint(100,599)]})
        elif stage=="local":
            for rs in ([500,200],[429],[400,200],[200,500],[500,400,200]):one({"to":"a","text":"x","responses":rs,"max_attempts":3})
            one({"to":"a","text":"x","max_attempts":0})
        else:
            for text in ("x"*160,"x"*161,"\U0001f680"*160,"\U0001f680"*161):one({"to":"a","text":text,"provider":"sms"})
    if f=="ledger":
        def t(i="x",amount=3,src="a",dst="b"):return {"op":"transfer","id":i,"from":src,"to":dst,"amount":amount}
        if stage=="initial":
            one({"balances":{"a":10,"b":0},"actions":[t(),t(),t(amount=4)]})
            one({"balances":{"a":2,"b":10},"actions":[t(),t("top",3,"b","a"),t()]})
            one({"balances":{"a":2,"b":0},"actions":[t(amount=True)]})
            one({"balances":{"a":10,"b":0},"actions":[t("__proto__"),t("__proto__")]})
            for _ in range(count):one({"balances":{"a":rng.randint(0,20),"b":20},"actions":[t(rng.choice(["x","y","z"]),rng.randint(1,12),*(rng.choice([("a","b"),("b","a")]))) for j in range(8)]})
        elif stage=="local":
            one({"balances":{"a":10,"b":0},"actions":[t(),{"op":"reverse","id":"x"},{"op":"reverse","id":"x"},t(),{"op":"reverse","id":"missing"}]})
            one({"balances":{"a":10,"b":0},"actions":[t(),t("back",3,"b","a"),{"op":"reverse","id":"x"}]})
        else:
            one({"balances":{"a":5,"b":0},"actions":[{"op":"batch","items":[t(),t("y",9)]},t(),{"op":"batch","items":[]}]})
            one({"balances":{"a":5,"b":0},"actions":[{"op":"batch","items":[t(),t()]},t()]})
            one({"balances":{"a":5,"b":0},"actions":[{"op":"batch","items":[t(),t(amount=True)]}]})
            one({"balances":{"a":10,"b":0},"actions":[{"op":"batch","items":[t(),t("fail",99)]},t("__proto__"),t("__proto__")]})
    if f=="scheduler":
        if stage=="initial":
            one({"jobs":[]});one({"jobs":[{"id":"a","deps":[]},{"id":"b","deps":["a"]}]});one({"jobs":[{"id":"a","deps":["a"]}]})
            one({"jobs":[{"id":"a","deps":["b"]}]});one({"jobs":[{"id":"a","deps":[]},{"id":"a","deps":[]}]})
            for _ in range(count):
                ids=[chr(97+i) for i in range(rng.randint(2,12))]
                jobs=[{"id":v,"deps":[u for u in ids[:i] if rng.random()<.3]} for i,v in enumerate(ids)];rng.shuffle(jobs);one({"jobs":jobs})
        elif stage=="local":
            for cap in (1,2,10,0,True):one({"jobs":[{"id":"a","deps":["z"]},{"id":"z","deps":[]},{"id":"b","deps":[]},{"id":"c","deps":[]}],"max_parallel":cap})
        else:
            for failed in ([],["a"],["b"],["missing"]):one({"jobs":[{"id":"a","deps":[]},{"id":"b","deps":["a"]},{"id":"c","deps":["b"]},{"id":"d","deps":[]}],"failed":failed})
            one({"jobs":[{"id":"a","deps":["b"]},{"id":"b","deps":["a"]}],"failed":["a"]})
    if f=="store":
        def p(i="x",v="one",**kw):return {"op":"put","id":i,"key":"a","value":v,**kw}
        get={"op":"get","key":"a"}
        if stage=="initial":
            seq([get,p()],[get,p("y","two"),p()],[get,p(v="changed")])
            seq([get,p()],[get],[p("z","three"),get],legacy={"format":1,"entries":{"a":{"value":"old","version":7}},"requests":{}})
            seq([{"op":"put","id":"","key":"a","value":"x"}],[get])
            special={"op":"put","id":"__proto__","key":"__proto__","value":"safe"}
            seq([special],[special,{"op":"get","key":"__proto__"}])
            for i in range(max(2,count//4)):
                v=str(rng.randrange(100000));seq([p("seed",v)],[get,p("seed",v),get])
        elif stage=="local":
            seq([p(expected=0)],[p("y","two",expected=0),p(expected=0)],[p("y","two",expected=1),get])
            seq([p(expected=1)],[p(expected=0),get])
        else:
            seq([p()],[{"op":"batch","items":[p("y","two",expected=1),p("z","three",expected=1)]}],[get,p("y","two",expected=1),get])
            seq([{"op":"batch","items":[p(expected=0),p("y","two",expected=1)]}],[get,p(expected=0),{"op":"batch","items":[]}])
    if f=="outbox":
        en=lambda i="a",v="one":{"op":"enqueue","id":i,"payload":v}
        take={"op":"take"}
        if stage=="initial":
            seq([take,en(),en("b","two")],[take,en()],[take,take,en("a","changed")])
            seq([take],[en("b","two"),take],[take],legacy={"format":1,"items":[{"id":"old","payload":"legacy"}]})
            for i in range(max(2,count//4)):seq([en("a",str(rng.randrange(100000)))],[take],[take])
        elif stage=="local":
            seq([en(),en("b")],[{"op":"cancel","id":"a"},take],[{"op":"cancel","id":"a"},en(),take])
        else:
            claim=lambda now:{"op":"claim","now":now,"lease":10}
            seq([en(),en("b","two"),claim(100)],[take,claim(109)],[claim(110),{"op":"ack","id":"a","token":1}],[{"op":"ack","id":"a","token":2},take,en()])
            seq([en(),claim(0)],[{"op":"ack","id":"a","token":1}],[claim(99)])
    return data


def suite_fingerprint(suite: list[Scenario]) -> str:
    payload=[vars(c) for c in suite]
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
