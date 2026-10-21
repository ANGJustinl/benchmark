"""Protocol fixture only; not a live model adapter."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from changebench.experiment import ReplayAdapter
request=json.load(sys.stdin)
result=ReplayAdapter().run(request)
print(json.dumps({"schema_version":1,"model_id":"reference-fixture","status":result.status,
                  "evidence_kind":"fixture","files":result.files,"usage":result.usage,
                  "skill_events":[]}))
