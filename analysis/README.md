# Portable aggregation of the first live pilot

```bash
python analysis/summarize.py
```

Standard-library Python only; no model calls, Docker or original workspace required.
Run from any working directory. The program verifies data/SHA256.json, deduplicates
stage keys within segments, excludes inherited stages, checks diagnostic coverage,
and compares recomputed totals with the expected published summary.

data/stages.csv contains 119 actual recorded executions across five segments. It
omits credentials, private CLI traces, response/thread IDs and local artifact paths.
data/cutoff-diagnostics.csv contains the 46 budget-stop diagnostic events. Gates and
one unreceipted in-flight start are explicitly separate in expected-summary.json.
Other tables capture the original frozen allocation summaries. Historical outputs
and model stages are not rerun by this analysis.

These are **derived observations**, not raw independently authenticated receipts.
Aggregation can be reproduced; source-file equality and semantic grades cannot be
independently established from the tables alone. The old evaluator discarded stdout
for successful cases. The source study checked 72 completed evaluation groups and
46 diagnostic groups for consistency, with two diagnostics newly executed in Docker.
It did not replay every historical candidate.

Source archive hashes are indexed in docs/provenance/evidence-index.json. Those
archives are not included or published by this PR. Original notebooks depend on the
full historical workspace and are not presented here as portable evidence.

Do not add adaptive attempts as independent samples, rank A/B/C on these completion
counts, double-count cached input, or equate known Token counts with a bill.
