"""Trusted reference fixtures, NOT model-generated benchmark submissions.

These deliberately support only the current stage. They validate the harness and
must never be exposed to a live builder or used to rank A/B/C effectiveness.
"""
from __future__ import annotations

from .catalog import STAGES, TASKS

HELPERS = '''import csv
import io
import json
import re
import tomllib

def integer(value, minimum=None, maximum=None):
    if type(value) is not int:
        raise ValueError("integer required")
    if minimum is not None and value < minimum:
        raise ValueError("below minimum")
    if maximum is not None and value > maximum:
        raise ValueError("above maximum")
    return value

def text(value):
    if not isinstance(value, str) or not value:
        raise ValueError("nonempty string required")
    return value
'''

SOURCES = {
"config": '''def solve(data):
    cfg = tomllib.loads(data["text"])
    if STAGE == "extension" and "profile" in data:
        profile = cfg.get("profiles", {})[text(data["profile"])]
        if not isinstance(profile, dict):
            raise ValueError("profile table required")
        cfg = {**cfg, **profile}
    host = cfg.get("host", "localhost")
    port = cfg.get("port", 8080)
    debug = cfg.get("debug", False)
    if STAGE == "local":
        env = data.get("env", {})
        if "APP_HOST" in env:
            host = env["APP_HOST"]
        if "APP_PORT" in env:
            raw = env["APP_PORT"]
            if not isinstance(raw, str) or not re.fullmatch(r"[0-9]+", raw):
                raise ValueError("decimal port required")
            port = int(raw)
        if "APP_DEBUG" in env:
            raw = env["APP_DEBUG"]
            if not isinstance(raw, str) or raw.lower() not in ("true", "false"):
                raise ValueError("boolean string required")
            debug = raw.lower() == "true"
    text(host)
    integer(port, 1, 65535)
    if type(debug) is not bool:
        raise ValueError("boolean required")
    return {"host": host, "port": port, "debug": debug}
''',
"records": '''def solve(data):
    threshold = integer(data.get("min_score", 0))
    fmt = data.get("format", "csv") if STAGE == "extension" else "csv"
    records = []
    if fmt == "csv":
        reader = csv.DictReader(io.StringIO(data["csv"]))
        if not reader.fieldnames or not {"name", "score"} <= set(reader.fieldnames):
            raise ValueError("headers missing")
        for row in reader:
            if None in row or row["score"] is None:
                raise ValueError("malformed row")
            raw = row["score"].strip()
            if not re.fullmatch(r"[+-]?[0-9]+", raw):
                raise ValueError("bad score")
            records.append({"name": text(row["name"]), "score": int(raw)})
    elif fmt == "jsonl":
        for line in data["text"].splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            records.append({"name": text(row["name"]), "score": integer(row["score"])})
    else:
        raise ValueError("unknown format")
    records = [row for row in records if row["score"] >= threshold]
    if STAGE == "local":
        descending = data.get("descending", False)
        if type(descending) is not bool:
            raise ValueError("boolean required")
        if descending:
            records.sort(key=lambda row: -row["score"])
    return {"rows": records, "total": sum(row["score"] for row in records)}
''',
"pricing": '''def solve(data):
    items = data["items"]
    subtotal = 0
    needed = {}
    for item in items:
        sku = text(item["sku"])
        price = integer(item["unit_price"], 0)
        qty = integer(item["qty"], 1)
        subtotal += price * qty
        needed[sku] = needed.get(sku, 0) + qty
    bps = integer(data.get("discount_bps", 0), 0, 10000)
    discount = subtotal * bps // 10000
    if STAGE == "local" and "discount_cap" in data:
        discount = min(discount, integer(data["discount_cap"], 0))
    quote = {"subtotal": subtotal, "discount": discount, "total": subtotal-discount}
    if STAGE == "extension":
        operation = data.get("operation", "quote")
        if operation not in ("quote", "place"):
            raise ValueError("unknown operation")
        if operation == "place":
            stock = dict(data["stock"])
            for qty in stock.values():
                integer(qty, 0)
            enough = all(stock.get(sku, 0) >= qty for sku, qty in needed.items())
            if enough:
                for sku, qty in needed.items():
                    stock[sku] = stock.get(sku, 0) - qty
            return {"status": "placed" if enough else "insufficient", "quote": quote, "stock": stock}
    return quote
''',
"delivery": '''def solve(data):
    provider = data.get("provider", "email")
    recipient, message = text(data["recipient"]), text(data["message"])
    allowed = ("email", "sms") if STAGE == "extension" else ("email",)
    if provider not in allowed or (provider == "sms" and len(message) > 160):
        raise ValueError("unsupported delivery")
    responses = data.get("responses", [200])
    if not responses:
        raise ValueError("responses required")
    for response in responses:
        integer(response, 100, 599)
    maximum = integer(data.get("max_attempts", 1), 1, 5) if STAGE == "local" else 1
    attempts = 0
    while attempts < maximum:
        status = responses[min(attempts, len(responses)-1)]
        attempts += 1
        success = 200 <= status < 300
        if success or not (status == 429 or status >= 500):
            break
    request = ({"to": recipient, "body": message} if provider == "email"
               else {"number": recipient, "text": message})
    return {"provider": provider, "attempts": attempts,
            "status": "sent" if success else "failed", "request": request}
''',
"inventory": '''def solve(data):
    available = integer(data["capacity"], 0)
    active, results = {}, []
    def reserve(rid, qty):
        nonlocal available
        if rid in active:
            return "reserved" if active[rid] == qty else "conflict"
        if available < qty:
            return "insufficient"
        available -= qty
        active[rid] = qty
        return "reserved"
    for action in data["actions"]:
        op = action["op"]
        if op == "reserve":
            result = reserve(text(action["id"]), integer(action["qty"], 1))
        elif op == "release" and STAGE == "local":
            rid = text(action["id"])
            if rid in active:
                available += active.pop(rid)
                result = "released"
            else:
                result = "missing"
        elif op == "batch" and STAGE == "extension":
            parsed = [(text(item["id"]), integer(item["qty"], 1)) for item in action["reservations"]]
            previous, previous_available = dict(active), available
            statuses = [reserve(rid, qty) for rid, qty in parsed]
            if all(status == "reserved" for status in statuses):
                result = "committed"
            else:
                active, available = previous, previous_available
                result = "rejected"
        else:
            raise ValueError("unknown operation")
        results.append(result)
    return {"available": available, "active": active, "results": results}
''',
"reports": '''def solve(data):
    minimum = integer(data.get("min_cents", 0), 0)
    records = []
    for row in data["records"]:
        name, cents = text(row["customer"]), integer(row["cents"], 0)
        if cents >= minimum:
            records.append({"customer": name, "cents": cents})
    if STAGE == "local":
        order = data.get("sort_by", "input")
        if order not in ("input", "customer"):
            raise ValueError("unknown order")
        if order == "customer":
            records.sort(key=lambda row: row["customer"])
    if STAGE == "extension":
        version = integer(data.get("version", 1))
        if version not in (1, 2):
            raise ValueError("unknown version")
        if version == 2:
            return {"schema": 2, "rows": [{**row, "currency": "USD"} for row in records],
                    "total_cents": sum(row["cents"] for row in records)}
    rows = []
    for row in records:
        cents = row["cents"]
        amount = f"{cents//100}.{cents%100:02d}"
        rows.append({"customer": row["customer"], "amount": amount})
    return {"rows": rows, "count": len(rows)}
''',
}
ENTRY = '''import json
import sys

for line in sys.stdin:
    try:
        value = solve(json.loads(line))
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        value = {"error": "invalid"}
    print(json.dumps(value, allow_nan=False, ensure_ascii=True), flush=True)
'''
MUTATIONS = {
    "config": ('integer(port, 1, 65535)', 'integer(int(port), 1, 65535)'),
    "records": ('row["score"] >= threshold', 'row["score"] > threshold'),
    "pricing": ('subtotal * bps // 10000', '(subtotal * bps + 9999) // 10000'),
    "delivery": ('200 <= status < 300', 'status == 200'),
    "inventory": ('return "reserved" if active[rid] == qty else "conflict"', 'return "conflict"'),
    "reports": ('amount = f"{cents//100}.{cents%100:02d}"', 'amount = "%.2f" % (cents / 100)'),
}


def reference_files(task: str, stage: str, layout: str = "flat", mutant: bool = False) -> dict[str, str]:
    from .poly_catalog import TASKS as POLY
    if task in POLY:
        from .poly_fixtures import reference_files as poly_reference
        if mutant:
            raise ValueError("Use named poly mutation controls, not the legacy mutation flag")
        return poly_reference(task, stage, layout)
    if task not in TASKS or stage not in STAGES or layout not in {"flat", "modular"}:
        raise ValueError("Invalid reference configuration")
    source = HELPERS + f'\nSTAGE = {stage!r}\n\n' + SOURCES[task]
    if mutant:
        old, new = MUTATIONS[task]
        if old not in source:
            raise AssertionError("Mutation target missing")
        source = source.replace(old, new, 1)
    if layout == "flat":
        return {"main.py": source + "\n" + ENTRY}
    return {"domain.py": source, "main.py": "from domain import solve\n" + ENTRY}
