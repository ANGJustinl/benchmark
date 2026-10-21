# Import provenance

Imported from the user-supplied ChangeBench v0.2.0 package and its local v0.3.0
repair copy. Historical sources and attribution remain under docs/history and
NOTICE.md. This is authored benchmark material, not a third-party task dataset.

Original v0.3.0 package source fingerprint:

```text
0a93357cc5e0261512e6a402457c0ef419e80a0f16726ba5ef1d7c8384a61b3b
```

imported-files.sha256.json pins the package, original tests, integration tests,
runtime definitions, examples, condition definitions, image builder and pyproject.
Run python scripts/verify_import.py. No original workspace is required. Package
bytes are unchanged; new docs/review scripts/CI are outside that fingerprint.

One existing trailing space in changebench/codex_adapter.py is intentionally retained
to preserve these exact bytes. It is a known inherited whitespace warning, not a
new adapter edit.

Old result trees, release wheels/hashes, private CLI traces, experiment-specific
recovery scripts, virtualenvs and temporary outputs were not copied wholesale.
Current analysis uses sanitized derived tables; see analysis/README.md for scope.

Original repository CC0 and imported ChangeBench MIT notices are both retained.
No release tag is created. Historical plans retain their original fingerprints;
a Git commit identifies each new review revision.
