# Working on ChangeBench

Read README.md, docs/experiment-contract.md, docs/agent-testing.md and
resource-policy.json before changing or running experiments.

- Installation, tests, reference calibration and replay must not call a model.
- This checkout is the evaluator/reviewer workspace. A participant must receive
  only a stage bundle in a separate workspace without reference implementations,
  hidden tests, future requests, credentials or the Docker socket.
- Candidate execution requires offline Docker. Never fall back to the host.
  Host execution is only for explicitly trusted bundled reference fixtures.
- Preserve frozen plans, scores, fingerprints and per-attempt provenance. Never
  pool adaptive recovery into original randomized results.
- Subsequent real experiments have no local aggregate/stage Token, agent time or
  tool-call caps: use null. Keep unknown usage null and deduplicate known receipts.
  Keep candidate test limits. Retry transient 529 with backoff; stop on quota/auth errors.
- Imported v0.3.0 planner/live adapters still implement historical finite limits.
  They are retained for compatibility, not approved defaults for new live work.
  Do not launch new paid work using them. The generic uncapped runner and new
  artifact-evaluation protocol are a separate planned change.
- Never commit credentials, private CLI traces, generated runs or virtualenvs.
- Run python scripts/verify_import.py to detect imported-baseline changes. Intended
  core changes need a separate commit and explicit provenance; do not silently
  rewrite the import manifest or historical source fingerprint.
- Record exact commands, environment, commit and limitations with test results.

Start independent review at docs/agent-testing.md. Reference replay is engineering
evidence, not evidence about model or Skill performance.
