# ChangeBench — reproducible review baseline

ChangeBench evaluates the cost of maintaining correct software. This branch imports
the v0.3.0 repair implementation for independent review: Python, TypeScript and Go,
eight task families, four provisional levels, and two independent changes from each
initial snapshot. Six older Python tasks remain in a separate regression track.

**Start with zero-model-call reproduction.** This checkout contains the evaluator
and reference answers; it is a reviewer workspace, not a participant workspace.

## Install

Use a POSIX host, Python 3.11+, Node 22.16.0, TypeScript 5.8.3 and Go 1.23.2.
Real Docker integration currently expects Linux amd64. Pinned versions support
reproduction; this is not a claim that they are current security releases.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
# Install Node and Go with your environment's trusted toolchain manager.
npm install --global --ignore-scripts typescript@5.8.3
python scripts/check_environment.py
python scripts/verify_import.py
```

Environment checks expose versions/readiness, never credential values. No setup or
reproduction command below calls a model or needs an API key.

## Reproduce

Every output directory must be new. Choose a writable absolute path.

```bash
python scripts/reproduce.py --tier unit --out /tmp/cb-unit-review
python scripts/reproduce.py --tier reference --out /tmp/cb-reference-review
```

`unit` runs the complete imported test suite. `reference` runs multilingual positive,
semantic-defect and no-change controls, repeated grades, full poly reference replay,
legacy replay and result auditing. It runs only **trusted bundled fixtures** on the
host. These are engineering checks, not new model performance samples.

For real candidate execution and Docker integration:

```bash
docker pull python:3.12.14-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
python scripts/build_runtime_images.py --out /tmp/cb-runtime-images.json
python scripts/reproduce.py --tier docker --images /tmp/cb-runtime-images.json --out /tmp/cb-docker-review
```

The build copies preinstalled pinned tools; candidates run offline. Missing Docker
or images fails explicitly, without a host fallback. See [runtime setup](docs/runtime-setup.md)
and [review tasks](docs/agent-testing.md). CI runs these checks without model credentials.

[Local fresh-clone validation receipt](docs/results/reproduction-2026-10-21.json):
370 unit tests, 14 real Docker tests, 433 calibration checks, and both reference
replay audits passed. This records local verification; GitHub CI is separate evidence.

## What the results mean

The first frozen live pilot covered eight **Python** families × A/B/C once: 24
trajectories. Recorded initial completions were 3/8, 3/8 and 4/8; full-chain success
was 0/8 in every arm. All 44 actually invoked artifacts had passing base-contract
results, combining regular evaluation and cutoff diagnostics.

Across adaptive recoveries, 46 budget-stop artifacts passed base-contract diagnosis;
48 saved-code resumptions left production Python files byte-identical, changing tests
or documentation. C recorded Skill loading in 2/8 original initial sessions.
The eventual **72/72 phases** are cumulative recovery closure, not a first-attempt
rate, Skill effect or calibrated leaderboard. TypeScript/Go model evidence is absent.

See [assessment](docs/results/EVALUATION.zh-CN.md), [portable analysis](analysis/README.md)
and [limitations](docs/limitations.md). Old successful case stdout was not retained:
record consistency audits are not independent semantic replays of every candidate.

## Live execution status

The package retains historical finite-budget planner/live interfaces for compatibility.
The uncapped pilot used an experiment-specific recovery controller, which is **not**
imported as a reusable runner. The user removed local total/stage Token, agent time
and tool-call caps for subsequent work; [resource-policy.json](resource-policy.json)
records null limits. **Do not launch new paid experiments with old defaults.**
A portable uncapped runner and separately versioned artifact-evaluation protocol
are the next PR. Credentials are configured externally and never mounted in candidates.

## Documentation

- [Independent review and handoff](docs/agent-testing.md)
- [Experiment contract](docs/experiment-contract.md)
- [Import provenance](docs/provenance/README.md)
- [Roadmap](docs/roadmap.zh-CN.md)
- [Historical design/audit/repair documents](docs/history/README.md)
- [License notices](NOTICE.md)

Public authored tasks are calibration material. Future private holdouts stay on the
evaluator side; participants receive only stage-specific public bundles.
