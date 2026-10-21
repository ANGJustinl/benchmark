# Sources and provenance

The design starts from the user's uploaded `deep-research-report.md` and the subsequent
agreed six-task A/B/C cold-maintainer design. The project is newly implemented here;
no benchmark scores or leaderboard claims from that report are treated as measured facts.

Verified primary references consulted on 2026-10-15:

1. SWE-bench harness API:
   https://www.swebench.com/SWE-bench/api/harness/
   Used only for the distinction between new-behavior checks and preserved-behavior checks.
   This implementation is not the SWE-bench harness and does not inherit its validation.

2. Agent Skills specification:
   https://agentskills.io/specification
   Used for metadata-first, body-on-demand loading and the SKILL.md structure.
   No universal token-saving effect is assumed.

3. OpenAI function calling guide:
   https://developers.openai.com/api/docs/guides/function-calling
   Used for Responses function_call / function_call_output round trips and preservation
   of reasoning items within tool-calling conversations.

4. OpenAI Responses API reference:
   https://developers.openai.com/api/reference/resources/responses/methods/create
   Used for measured input/output usage and cached/reasoning token breakdowns.
   Actual API requests were not executed in the delivered selftest.

5. OpenAI harness-engineering experience report:
   https://openai.com/index/harness-engineering/
   Background only. Its repository-specific architectural constraints are not evidence
   that more, or fewer, constraints universally improve coding agents.

All task cases, reference fixtures, scoring choices, thresholds and implementation are
our proposed benchmark design, not requirements established by these sources. No code,
license terms, repository dates or model scores from earlier conversational claims are
relied on without new verification. The six public microtasks are calibration material;
private held-out task families are required for stronger generalization claims.


## v0.2 methodology references

The following primary material was consulted for the extension, not used as imported
datasets or reproduced leaderboard experiments:

- OpenAI, Evaluation best practices: https://developers.openai.com/api/docs/guides/evaluation-best-practices
  Motivation for explicit uncertainty, focused evals and evidence before adding complexity.
- Docker, resource constraints: https://docs.docker.com/engine/containers/resource_constraints
  Limits constrain resources but do not make a shared machine exclusive.
- Docker, security: https://docs.docker.com/engine/security/
  Defense-in-depth framing, not a proof of sandbox security.
- SWE-PolyBench (2025): https://arxiv.org/abs/2504.08703
  Multilingual repository-level evaluation is broader than the authored microtasks here.
- PolyCodeEval (2026): https://arxiv.org/abs/2610.11618
  Motivation to separate language and granularity rather than equating all coding tasks.
- SWE-CI: https://github.com/SKYLENAGE-AI/SWE-CI
  Related direction in ongoing software maintenance, not the implementation used here.

Our 24 new variants are eight independently specified problem families, NOT 24 independent
projects. Positive fixture styles share business logic, and all languages were authored
in one effort. No independent review team, contamination-free holdout, container run, or
real-model performance result is claimed. The implementation and numeric findings are
supported by the bundled test logs rather than these external sources.
