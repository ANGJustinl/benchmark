# Independent agent testing

## Reviewer assignment (default)

Record the exact commit SHA and start from a fresh clone. Follow README installation
and run scripts/check_environment.py, scripts/verify_import.py and the unit,
reference and Docker tiers of scripts/reproduce.py. No API key or model calls needed.

| Review area | Questions | Evidence |
| --- | --- | --- |
| Environment | Fresh install outside the original workspace? Three native runtimes? | Versions, commands, exit codes, JUnit/summary |
| Semantic evaluator | Plausible broken implementation passes? Oracle/port contract errors? | Minimal input, expected/observed output, task/stage |
| Leakage | Starters/stage exports reveal future changes or answers? | Exact path and reproducer |
| Integrity/runtime | Wrong counts, absent cases, startup failures or disk forks misclassified? | Fault injection and expected rejection |
| Methodology | Correct denominators, recovery, unknown use and treatment exposure? | Recomputed values and explicit scope |

Review sequentially or assign focused areas. This document does not itself launch
agents. Reviewers can see full references because they are reviewing the evaluator.
Do not manufacture architecture rankings from file counts or subjective scores.

Report: commit, OS/tool versions, exact command, expected/actual behavior, minimum
reproducer, impact, fix and executed checks. If no issue is found, still provide
receipts and limitations. Mark unexecuted Docker as unexecuted. CI and local checks
are separate evidence. Existing negative controls are not exhaustive mutation coverage.

reproduce.py writes commands.jsonl, reference/JUnit outputs and summary.json. A stale
running status is not evidence of a live process or a completed valid experiment.

## Participant assignment (separate workspace)

A participant must not receive this full checkout. A different directory or branch
does not hide oracle, reference fixtures or future requirements.

The evaluator exports only current-stage public material into a separate workspace
and restricts model tools to that bundle. `changebench export-task` exports initial
public material; later handoffs must be controlled by the evaluator. No hidden tests,
future requests, original chat, credentials or Docker socket may be readable.
Candidate grading stays offline Docker.

Do not count someone who inspected answers as a blinded participant on those tasks.
Public old tasks are calibration material. New private holdouts stay on the evaluator
side, outside participant tools' filesystem access.

## Paid experiments are separate

Do not launch historical bounded live examples. The generic null-limit runner is
pending. Future paid work requires an explicit experiment task, external credential
configuration, frozen plan and separate recovery reporting.
