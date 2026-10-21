"""Public specifications and private, independently calculated test cases.

A model receives only public_spec(task, stage), never cases(), seeds, or this module.
"""
from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass
from pathlib import Path

TASKS = ("config", "records", "pricing", "delivery", "inventory", "reports")
STAGES = ("initial", "local", "extension")
COMMON = """Implement a Python program at main.py. It must read one JSON object per stdin
line, and write exactly one JSON value per input line to stdout. It must handle multiple
lines in one process; lines are independent scenarios. Diagnostic output goes to stderr.
Use Python >=3.11 standard library only. Internal files/functions/classes are unrestricted.
For any validation error specified below, return {"error":"invalid"}, then continue.
Unless specified, inputs have the documented JSON types. Reject non-finite numbers.
Integers in the specifications exclude booleans. Unknown optional input keys may be ignored.
No network, real accounts, filesystem paths, or external providers are involved.
"""
SPECS = {
"config": {
 "kind": "greenfield",
 "initial": """Build a TOML configuration loader. Input: {text: TOML string}.
Read host (nonempty string, default localhost), port (integer 1..65535, default 8080),
debug (boolean, default false). Return exactly {host,port,debug}. Ignore unknown TOML keys.
Invalid TOML or invalid final values return the validation error. Whitespace in a nonempty
host is preserved. This is a small application, not a configuration framework.""",
 "local": """Add optional env object. APP_HOST overrides host as a string; APP_PORT must be
a nonempty ASCII decimal-digit string; APP_DEBUG is case-insensitive true or false only.
Apply overrides before validating final values. Ignore unrelated env keys. Missing env
retains the old behavior. Empty or malformed recognized env values are invalid.""",
 "extension": """Add optional profile string. TOML [profiles.NAME] is a named configuration
mapping. Overlay its host/port/debug values on root values before final validation.
Requested profile must exist and be a table. Without profile retain the old behavior.
This branch does not require environment override support.""",
},
"records": {
 "kind": "greenfield",
 "initial": """Build a CSV record filter. Input: {csv: string,min_score: integer=0}.
CSV must have name and score headers, in any order; extra headers are ignored. Parse score
as a signed base-10 integer (outer whitespace permitted). name is a nonempty string,
preserved exactly. Validate every row, even rows that will be filtered out. Keep score
>= min_score in input order. Return {rows:[{name,score}],total:sum of retained scores}.
A header-only CSV is valid. Malformed rows or missing required headers are invalid.""",
 "local": """Add descending: boolean=false. When true, sort retained records by descending
score, preserving original order among ties. The default retains the old behavior.""",
 "extension": """Add format: csv|jsonl (default csv). For jsonl use text: string; each nonblank
line is a JSON object with name:string and score:integer; extra fields are ignored.
Apply the existing validation/filter/total behavior. Empty JSONL is valid. Preserve CSV
support unchanged; this branch does not require descending support.""",
},
"pricing": {
 "kind": "brownfield",
 "initial": """Extend the supplied legacy quote program without changing its existing valid
quotes. Input: {items:[{sku:nonempty string,unit_price:integer>=0,qty:integer>0}],
discount_bps:integer=0}. Validate discount_bps in 0..10000. Return {subtotal,discount,total}:
subtotal=sum(price*qty); discount=floor(subtotal*discount_bps/10000); total=subtotal-discount.
Empty items are valid. Add validation of invalid input while preserving valid behavior.""",
 "local": """Add optional discount_cap:integer>=0, limiting the computed discount in cents.
If absent there is no cap. Keep all previous quote behavior and validation.""",
 "extension": """Add operation:quote|place (default quote). place also takes stock mapping sku
to nonnegative integer quantity; a missing sku has zero stock. Aggregate duplicate SKU
quantities. If all items can be fulfilled, return {status:placed,quote:existing_quote,
stock:updated_copy}; otherwise {status:insufficient,quote:existing_quote,stock:unchanged_copy}.
Do not partially decrement stock. Ordinary quote output must stay unchanged. This branch
does not require discount_cap support.""",
},
"delivery": {
 "kind": "greenfield",
 "initial": """Build a deterministic notification sender using supplied responses, no network.
Input: {recipient:nonempty string,message:nonempty string,provider:email(default),
responses:[integer HTTP statuses 100..599]=[200]}. The list must be nonempty.
Try the first response only. Return {provider,attempts:1,status:sent|failed,
request:{to:recipient,body:message}}. A 2xx response is sent; everything else is failed.
Initially only email is supported; unknown providers are invalid.""",
 "local": """Add max_attempts:integer 1..5 (default 1). Retry only status 429 and 5xx.
Stop on success or a nonretryable response. If responses are exhausted, reuse the last
status. attempts counts actual attempts. Existing single-attempt behavior remains.""",
 "extension": """Add provider sms. Its message must be at most 160 Unicode code points and
its request is {number:recipient,text:message}, not email's request format. The old email
behavior, including no message-length limit, remains unchanged. This branch does not
require retry support.""",
},
"inventory": {
 "kind": "greenfield",
 "initial": """Build a reservation state machine. Input: {capacity:integer>=0,actions:[...]};
start with capacity available and no active reservations for each input line.
reserve action is {op:reserve,id:nonempty string,qty:integer>0}. Existing same id+qty is
idempotent: reserved without another debit. Existing id with different qty yields conflict.
Insufficient capacity yields insufficient and creates no reservation. Return
{available,active:{id:qty},results:[status per action]}. Invalid actions invalidate the
whole scenario with the standard error. Only reserve is initially supported.""",
 "local": """Add {op:release,id:nonempty string}. Release an active reservation exactly once,
restore capacity, and return released. Missing/already released returns missing. Reuse of
an id after release is allowed. Keep all reserve behavior.""",
 "extension": """Add {op:batch,reservations:[{id,qty},...]}. Apply the same reserve rules
sequentially inside a batch. If every item succeeds, commit and return committed;
otherwise roll back the entire batch and return rejected. An empty batch commits.
Invalid id/qty remains a validation error, not rejected. Keep single reserve behavior.
Serial execution is permitted; no claim of OS-level concurrency or durable storage is made.""",
},
"reports": {
 "kind": "brownfield",
 "initial": """Repair and extend the supplied legacy revenue exporter. Input: {records:[
{customer:nonempty string,cents:integer>=0}],min_cents:integer>=0=0}.
Filter rows with cents >= min_cents; do not combine duplicates. Return legacy shape
{rows:[{customer,amount:decimal string with exactly two digits}],count:integer}.
Preserve input order and original names. Use exact cents arithmetic, not floating point.
Empty records are valid. Add validation without breaking valid old output.""",
 "local": """Add sort_by: input|customer (default input). customer is case-sensitive ascending
lexicographic order and stable for duplicate names. Keep legacy shape and filtering.""",
 "extension": """Add version:integer=1. Version 1 retains the exact legacy shape.
Version 2 returns {schema:2,rows:[{customer,cents,currency:USD}],total_cents:sum} after
existing filtering, in input order. Other versions are invalid. Do not add schema or
currency fields to version 1. This branch does not require sort_by support.""",
}}


@dataclass(frozen=True)
class Case:
    id: str
    origin: str
    input: dict
    expected: dict


def public_spec(task: str, stage: str) -> str:
    from .poly_catalog import TASKS as POLY, public_spec as public_poly
    if task in POLY:
        return public_poly(task, stage)
    if task not in TASKS or stage not in STAGES:
        raise ValueError("Unknown task or stage")
    text = COMMON + "\nCURRENT REQUIREMENTS\n" + SPECS[task]["initial"]
    if stage != "initial":
        text += "\nCHANGE REQUEST\n" + SPECS[task][stage]
    return text


def initial_files(task: str) -> dict[str, str]:
    from .poly_catalog import TASKS as POLY
    if task in POLY:
        from .poly_fixtures import initial_files as poly_initial
        return poly_initial(task)
    files = {
      "README.md": public_spec(task, "initial"),
      "pyproject.toml": '[project]\nname="candidate-project"\nversion="0.0.0"\nrequires-python=">=3.11"\n',
    }
    if task == "pricing":
        files["legacy.py"] = '''def quote(data):
    subtotal = sum(x["unit_price"] * x["qty"] for x in data["items"])
    discount = subtotal * data.get("discount_bps", 0) // 10000
    return {"subtotal": subtotal, "discount": discount, "total": subtotal - discount}
'''
        files["main.py"] = 'import json, sys\nfrom legacy import quote\nfor line in sys.stdin:\n    print(json.dumps(quote(json.loads(line))))\n'
    elif task == "reports":
        files["legacy.py"] = '''def export(data):
    rows = [{"customer": r["customer"], "amount": "%.2f" % (r["cents"] / 100)}
            for r in data["records"] if r["cents"] >= data.get("min_cents", 0)]
    return {"rows": rows, "count": len(rows)}
'''
        files["main.py"] = 'import json, sys\nfrom legacy import export\nfor line in sys.stdin:\n    print(json.dumps(export(json.loads(line))))\n'
    else:
        files["main.py"] = '# Implement the documented JSON-lines interface.\n'
    sample = cases(task, "initial", 11, random_count=0)[0]
    files["test_public.py"] = '''import json, pathlib, subprocess, sys, unittest

class PublicExample(unittest.TestCase):
    def test_example(self):
        p = subprocess.run([sys.executable, "main.py"], input=json.dumps(INPUT)+"\\n",
                           text=True, capture_output=True, timeout=3,
                           cwd=pathlib.Path(__file__).parent)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout), EXPECTED)

INPUT = ''' + repr(sample.input) + '\nEXPECTED = ' + repr(sample.expected) + '\n\nif __name__ == "__main__":\n    unittest.main()\n'
    return files


def cases(task: str, stage: str, seed: int, random_count: int = 18) -> list[Case]:
    """Initial cases are identical in both forks; future inputs never enter T0."""
    if task not in TASKS or stage not in STAGES:
        raise ValueError("Unknown task or stage")
    r = random.Random(f"{task}:{seed}")
    out: list[Case] = []
    def add(inp: dict, expected: dict, origin: str = "initial") -> None:
        out.append(Case(f"{origin}-{len(out):03}", origin, inp, expected))
    invalid = {"error": "invalid"}
    if task == "config":
        normal = {"host": "localhost", "port": 8080, "debug": False}
        add({"text": ""}, normal)
        for p in (0, 65536, -1): add({"text": f"port={p}"}, invalid)
        for txt in ('port=true', 'host=""', 'debug="false"', '[broken'):
            add({"text": txt}, invalid)
        for _ in range(random_count):
            p = r.randint(1, 65535); d = r.choice([True, False]); h = f"h{r.randrange(10000)}"
            add({"text": f'host="{h}"\nport={p}\ndebug={str(d).lower()}\nextra=42'},
                {"host": h, "port": p, "debug": d})
        if stage == "local":
            add({"text": "port=0", "env": {"APP_PORT": "9090", "APP_DEBUG": "TrUe"}},
                {**normal, "port": 9090, "debug": True}, stage)
            for v in ("", "-1", "+80", "1.5", "65536", "true"):
                add({"text": "", "env": {"APP_PORT": v}}, invalid, stage)
            add({"text": "", "env": {"APP_HOST": "x", "OTHER": "y"}}, {**normal,"host":"x"}, stage)
            add({"text": "", "env": {"APP_DEBUG": "yes"}}, invalid, stage)
        elif stage == "extension":
            add({"text": 'host="root"\n[profiles.prod]\nport=9000', "profile":"prod"},
                {**normal,"host":"root","port":9000}, stage)
            add({"text": '[profiles.dev]\ndebug=true',"profile":"dev"}, {**normal,"debug":True}, stage)
            for inp in ({"text":"", "profile":"x"}, {"text":"[profiles.p]\nport=0","profile":"p"},
                        {"text":"[profiles]\np=1", "profile":"p"}): add(inp, invalid, stage)
    elif task == "records":
        add({"csv":"name,score\na,2\nb,-1\n"}, {"rows":[{"name":"a","score":2}],"total":2})
        add({"csv":"score,name\n"}, {"rows":[],"total":0})
        add({"csv":'name,score\n"a,b",0\n'}, {"rows":[{"name":"a,b","score":0}],"total":0})
        for txt in ("a,b\nx,1\n", "name,score\na,no\n", "name,score\n,1\n", "name,score\na\n"):
            add({"csv":txt},invalid)
        for _ in range(random_count):
            scores=[r.randint(-9,12) for i in range(r.randint(0,8))]; floor=r.randint(-3,5)
            rows=[{"name":f"n{i}","score":v} for i,v in enumerate(scores) if v>=floor]
            add({"csv":"name,score\n"+"".join(f"n{i},{v}\n" for i,v in enumerate(scores)),"min_score":floor},
                {"rows":rows,"total":sum(v["score"] for v in rows)})
        if stage == "local":
            rows=[{"name":"b","score":5},{"name":"c","score":5},{"name":"a","score":1}]
            add({"csv":"name,score\na,1\nb,5\nc,5\n","descending":True}, {"rows":rows,"total":11},stage)
            add({"csv":"name,score\n","descending":True}, {"rows":[],"total":0},stage)
            add({"csv":"name,score\na,1\n","descending":"yes"},invalid,stage)
        elif stage == "extension":
            add({"format":"jsonl","text":'{"name":"x","score":4}\n\n{"name":"y","score":-2}',"min_score":0},
                {"rows":[{"name":"x","score":4}],"total":4},stage)
            add({"format":"jsonl","text":""}, {"rows":[],"total":0},stage)
            for txt in ('{"name":"a","score":true}', '{"name":"a","score":"2"}', '{broken'):
                add({"format":"jsonl","text":txt},invalid,stage)
    elif task == "pricing":
        add({"items":[{"sku":"a","unit_price":199,"qty":2}],"discount_bps":1000},
            {"subtotal":398,"discount":39,"total":359})
        add({"items":[]}, {"subtotal":0,"discount":0,"total":0})
        for key,val in (("unit_price",-1),("qty",0),("qty",True),("sku","")):
            item={"sku":"x","unit_price":100,"qty":1};item[key]=val;add({"items":[item]},invalid)
        add({"items":[],"discount_bps":10001},invalid)
        for _ in range(random_count):
            price=r.randint(1,100000);qty=r.randint(1,9);bp=r.randint(0,10000)
            sub=price*qty;disc=(sub*bp)//10000
            add({"items":[{"sku":"z","unit_price":price,"qty":qty}],"discount_bps":bp},
                {"subtotal":sub,"discount":disc,"total":sub-disc})
        if stage == "local":
            for cap in (0,1,50,500):
                add({"items":[{"sku":"x","unit_price":1000,"qty":1}],"discount_bps":1000,"discount_cap":cap},
                    {"subtotal":1000,"discount":min(100,cap),"total":1000-min(100,cap)},stage)
            add({"items":[],"discount_cap":-1},invalid,stage)
        elif stage == "extension":
            items=[{"sku":"x","unit_price":50,"qty":2},{"sku":"x","unit_price":50,"qty":1}]
            quote={"subtotal":150,"discount":0,"total":150}
            for stock in ({"x":3,"z":9},{"x":2,"z":9},{}):
                ok=stock.get("x",0)>=3; updated=dict(stock)
                if ok:updated["x"]-=3
                add({"operation":"place","items":items,"stock":stock},
                    {"status":"placed" if ok else "insufficient","quote":quote,"stock":updated},stage)
            add({"operation":"place","items":[],"stock":{"x":-1}},invalid,stage)
    elif task == "delivery":
        def expected(p,m,s,a=1):
            return {"provider":p,"attempts":a,"status":"sent" if 200<=s<300 else "failed",
                    "request":({"to":"u","body":m} if p=="email" else {"number":"u","text":m})}
        add({"recipient":"u","message":"hi"},expected("email","hi",200))
        for status in (199,200,201,204,299,300,429,500,599):
            add({"recipient":"u","message":"m","responses":[status]},expected("email","m",status))
        for inp in ({"recipient":"","message":"m"},{"recipient":"u","message":""},
                    {"recipient":"u","message":"m","responses":[]},
                    {"recipient":"u","message":"m","provider":"unknown"}):add(inp,invalid)
        for _ in range(random_count):
            status=r.randint(100,599)
            add({"recipient":"u","message":"hello","responses":[status,200]},expected("email","hello",status))
        if stage == "local":
            for seq,n,final,attempts in (([500,200],3,200,2),([400,200],3,400,1),([429],3,429,3),([503,204],1,503,1)):
                add({"recipient":"u","message":"m","responses":seq,"max_attempts":n},expected("email","m",final,attempts),stage)
            add({"recipient":"u","message":"m","max_attempts":0},invalid,stage)
        elif stage == "extension":
            for msg in ("hi","x"*160):
                add({"provider":"sms","recipient":"u","message":msg},expected("sms",msg,200),stage)
            add({"provider":"sms","recipient":"u","message":"x"*161},invalid,stage)
            add({"recipient":"u","message":"x"*161},expected("email","x"*161,200),stage)
    elif task == "inventory":
        def res(i,q):return {"op":"reserve","id":i,"qty":q}
        add({"capacity":3,"actions":[res("a",2),res("a",2),res("b",2)]},
            {"available":1,"active":{"a":2},"results":["reserved","reserved","insufficient"]})
        add({"capacity":3,"actions":[res("a",2),res("a",1)]},
            {"available":1,"active":{"a":2},"results":["reserved","conflict"]})
        add({"capacity":0,"actions":[]},{"available":0,"active":{},"results":[]})
        for inp in ({"capacity":-1,"actions":[]},{"capacity":3,"actions":[res("a",0)]},
                    {"capacity":3,"actions":[res("",1)]},{"capacity":True,"actions":[]}):add(inp,invalid)
        for _ in range(random_count):
            cap=r.randint(0,20);q=r.randint(1,20);ok=q<=cap
            add({"capacity":cap,"actions":[res("x",q),res("x",q)]},
                {"available":cap-q if ok else cap,"active":{"x":q} if ok else {},
                 "results":["reserved" if ok else "insufficient"]*2})
        if stage == "local":
            add({"capacity":4,"actions":[res("a",3),{"op":"release","id":"a"},
                 {"op":"release","id":"a"},res("a",4)]},
                {"available":0,"active":{"a":4},"results":["reserved","released","missing","reserved"]},stage)
            add({"capacity":0,"actions":[{"op":"release","id":"x"}]},
                {"available":0,"active":{},"results":["missing"]},stage)
        elif stage == "extension":
            add({"capacity":5,"actions":[{"op":"batch","reservations":[{"id":"a","qty":3},{"id":"b","qty":3}]},res("c",5)]},
                {"available":0,"active":{"c":5},"results":["rejected","reserved"]},stage)
            add({"capacity":4,"actions":[{"op":"batch","reservations":[{"id":"a","qty":2},{"id":"a","qty":2}]}]},
                {"available":2,"active":{"a":2},"results":["committed"]},stage)
            add({"capacity":4,"actions":[{"op":"batch","reservations":[]}]},
                {"available":4,"active":{},"results":["committed"]},stage)
            add({"capacity":4,"actions":[{"op":"batch","reservations":[{"id":"a","qty":0}]}]},invalid,stage)
    else:
        add({"records":[{"customer":"x","cents":123}]}, {"rows":[{"customer":"x","amount":"1.23"}],"count":1})
        add({"records":[]},{"rows":[],"count":0})
        for inp in ({"records":[{"customer":"x","cents":-1}]},
                    {"records":[{"customer":"","cents":0}]},{"records":[],"min_cents":-1}):add(inp,invalid)
        for _ in range(random_count):
            n=r.choice([r.randint(0,99999),10**19+r.randint(0,1000)]);floor=r.choice([0,n,n+1])
            rows=[{"customer":"x","amount":f"{n//100}.{n%100:02d}"}] if n>=floor else []
            add({"records":[{"customer":"x","cents":n}],"min_cents":floor}, {"rows":rows,"count":len(rows)})
        if stage == "local":
            records=[{"customer":"b","cents":1},{"customer":"a","cents":2},{"customer":"a","cents":3}]
            add({"records":records,"sort_by":"customer"},{"rows":[{"customer":"a","amount":"0.02"},
                {"customer":"a","amount":"0.03"},{"customer":"b","amount":"0.01"}],"count":3},stage)
            add({"records":[],"sort_by":"bad"},invalid,stage)
        elif stage == "extension":
            add({"records":[{"customer":"x","cents":123},{"customer":"y","cents":0}],"version":2},
                {"schema":2,"rows":[{"customer":"x","cents":123,"currency":"USD"},
                 {"customer":"y","cents":0,"currency":"USD"}],"total_cents":123},stage)
            add({"records":[],"version":2},{"schema":2,"rows":[],"total_cents":0},stage)
            add({"records":[],"version":3},invalid,stage)
    if stage != "initial":
        for inp, expected in branch_cases(task, stage, r, random_count):
            add(inp, expected, stage)
    return copy.deepcopy(out)


def branch_cases(task: str, stage: str, rng: random.Random, count: int) -> list[tuple[dict, dict]]:
    """Independent randomized follow-up examples; not sourced from the reference code."""
    result = []
    for i in range(count):
        if task == "config":
            port = rng.randint(1, 65535)
            if stage == "local":
                inp = {"text": "port=0", "env": {"APP_PORT": str(port).zfill(6), "APP_HOST": f"host-{i}"}}
                expected = {"host": f"host-{i}", "port": port, "debug": False}
            else:
                inp = {"text": f'host="root"\n[profiles.p{i}]\nport={port}', "profile": f"p{i}"}
                expected = {"host": "root", "port": port, "debug": False}
        elif task == "records":
            values = [rng.randint(-3, 8) for _ in range(9)]
            threshold = rng.randint(0, 5)
            all_rows = [{"name": f"n{j}", "score": x} for j, x in enumerate(values)]
            selected = [x for x in all_rows if x["score"] >= threshold]
            if stage == "local":
                inp = {"csv": "name,score\n" + "".join(f"n{j},{x}\n" for j, x in enumerate(values)),
                       "min_score": threshold, "descending": True}
                selected = sorted(selected, key=lambda row: -row["score"])
            else:
                inp = {"format": "jsonl", "text": "\n".join(json.dumps(row) for row in all_rows), "min_score": threshold}
            expected = {"rows": selected, "total": sum(x["score"] for x in selected)}
        elif task == "pricing":
            price, qty, bps = rng.randint(1, 5000), rng.randint(1, 5), rng.randint(0, 10000)
            subtotal = price * qty
            discount = subtotal * bps // 10000
            inp = {"items": [{"sku": "x", "unit_price": price, "qty": qty}], "discount_bps": bps}
            if stage == "local":
                cap = rng.randint(0, max(1, discount))
                inp["discount_cap"] = cap
                discount = min(discount, cap)
                expected = {"subtotal": subtotal, "discount": discount, "total": subtotal-discount}
            else:
                have = rng.choice([qty, qty-1, qty+4])
                inp.update(operation="place", stock={"x": have, "spare": 17})
                expected = {"quote": {"subtotal": subtotal, "discount": discount, "total": subtotal-discount},
                            "status": "placed" if have >= qty else "insufficient",
                            "stock": {"x": have-qty if have >= qty else have, "spare": 17}}
        elif task == "delivery":
            if stage == "local":
                statuses = rng.choice([[500, 429, 201], [503], [400, 200], [204, 500]])
                limit = rng.randint(1, 5)
                visited = []
                for j in range(limit):
                    code = statuses[min(j, len(statuses)-1)]
                    visited.append(code)
                    if code != 429 and not 500 <= code <= 599:
                        break
                inp = {"recipient": "u", "message": "m", "responses": statuses, "max_attempts": limit}
                expected = {"provider": "email", "attempts": len(visited),
                            "status": "sent" if 200 <= visited[-1] <= 299 else "failed",
                            "request": {"to": "u", "body": "m"}}
            else:
                message = "s" * rng.randint(1, 165)
                inp = {"provider": "sms", "recipient": "u", "message": message}
                expected = ({"error": "invalid"} if len(message) > 160 else
                            {"provider": "sms", "attempts": 1, "status": "sent", "request": {"number": "u", "text": message}})
        elif task == "inventory":
            capacity = rng.randint(1, 20)
            if stage == "local":
                qty = rng.randint(1, capacity)
                inp = {"capacity": capacity, "actions": [{"op": "reserve", "id": "a", "qty": qty},
                      {"op": "release", "id": "a"}, {"op": "release", "id": "a"},
                      {"op": "reserve", "id": "a", "qty": capacity}]}
                expected = {"available": 0, "active": {"a": capacity}, "results": ["reserved", "released", "missing", "reserved"]}
            else:
                qa, qb = rng.randint(1, 15), rng.randint(1, 15)
                inp = {"capacity": capacity, "actions": [{"op": "batch", "reservations": [{"id": "a", "qty": qa}, {"id": "b", "qty": qb}]},
                      {"op": "reserve", "id": "c", "qty": capacity}]}
                if qa+qb <= capacity:
                    expected = {"available": capacity-qa-qb, "active": {"a": qa, "b": qb}, "results": ["committed", "insufficient"]}
                else:
                    expected = {"available": 0, "active": {"c": capacity}, "results": ["rejected", "reserved"]}
        else:
            records = [{"customer": rng.choice(["a", "b", "c"]), "cents": rng.randint(0, 10000)} for _ in range(8)]
            threshold = rng.randint(0, 5000)
            selected = [row for row in records if row["cents"] >= threshold]
            inp = {"records": records, "min_cents": threshold}
            if stage == "local":
                inp["sort_by"] = "customer"
                selected = sorted(selected, key=lambda row: row["customer"])
                expected = {"rows": [{"customer": row["customer"], "amount": "%d.%02d" % divmod(row["cents"], 100)} for row in selected],
                            "count": len(selected)}
            else:
                inp["version"] = 2
                expected = {"schema": 2, "rows": [{**row, "currency": "USD"} for row in selected],
                            "total_cents": sum(row["cents"] for row in selected)}
        result.append((inp, expected))
    return result
