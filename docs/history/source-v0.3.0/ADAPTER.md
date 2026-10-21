# Adapter contract v1

The runner calls a **trusted executable**, not agent-generated code. The worker
gets a single JSON object on stdin and returns a single JSON object on stdout.
No shell parsing is used. Logs must go to stderr.

## Request

- `schema_version`: 1.
- `model_id`: an explicitly configured model identifier.
- `task_key`, `stage`: current task and current stage only.
- `public_task`: initial requirements plus, for maintenance, that branch's change.
- `files`: complete project snapshot as relative-path -> UTF-8-text mapping.
- `instructions`: A baseline or B baseline + canonical design policy.
- `skill_catalog`: C metadata only; empty in A, B and all maintenance sessions.
- `skill_files`: trusted-worker resources. **Never concatenate into the initial prompt.**
- `generation_seed`: requested generation seed, not the hidden-test seed. Report
  unsupported seed control honestly. The bundled Responses worker does not send a seed.
- `budget`: wall time, aggregate input+output token budget, and tool-call budget.
- `tool_image`: controller-resolved immutable Docker image ID for live tool execution.
- `adapter_settings`: worker-specific settings. Use the same values across all arms.

The evaluator's expected answers, test generator, test seed, reference implementations,
other arms' artifacts and other future branches must not enter the worker's model context.
A worker may expose project files through tools. It must not expose its own host filesystem.

## Response

```json
{
  "schema_version": 1,
  "evidence_kind": "live_model",
  "model_id": "your-pinned-model-id",
  "status": "completed",
  "files": {"main.py": "..."},
  "usage": {
    "input_tokens": 1000,
    "output_tokens": 500,
    "cached_input_tokens": 200,
    "reasoning_tokens": 100,
    "tool_calls": 6
  },
  "skill_events": [],
  "api_calls": [],
  "verification_events": [],
  "seed_supported": false
}
```

All counters are actual measurements or `null`. Cached input tokens are a subset
of input tokens; reasoning tokens are a subset of output tokens. Do not add them twice.
`files` is a complete final snapshot, not a patch. Binary artifacts are out of scope.
Allowed statuses: `completed`, `budget_exhausted`, `infra_error`, `model_error`.
A completion claim does not determine grading: the controller independently evaluates
behavior in another process/container after the worker has terminated.

`fixture` evidence is rejected by the live command adapter. The reference adapter is
only enabled explicitly in protocol tests. Do not label simulated costs as measurements.

## Included Responses worker

`examples/live-config.json` invokes packaged `worker_main.py`. It uses the Responses
API through Python's standard-library HTTPS client. All arms within a task receive the same tools. Legacy tasks retain Python execution;
poly tasks instead provide native `run_project` and `test_project` tools alongside
list, read, write, delete and load-skill. The latter test tool runs unittest, node --test,
or go test against candidate-authored tests only. C gets metadata, and the full
skill enters the context only after a `read_skill` call. It is a **controlled skill-loading
implementation**, not a claim about every native Codex or Claude client.

Every stage starts a new worker and a new conversation. There is no shared conversation
ID, previous-response pointer, retrieval memory, or inherited experimental AGENTS.md.
Opaque reasoning items are carried within one API conversation when needed, but are
not exported into audit artifacts. API response IDs, model IDs and usage are recorded.

`OPENAI_API_KEY` lives only in the trusted host worker. Native tool execution runs in
a disposable, offline Docker container containing only the submitted project. No Docker
socket, host evaluator directory, credential file or host environment is mounted.
The controller tags and cleans tool containers even if a worker hits its hard wall timeout.

## Budget and failure semantics

Wall time and tool-call counts are enforced. Token totals are checked after each API
response and by the controller; input size is not known exactly before billing. Thus
the token limit is **not an absolute billing cap**. A single request can overshoot it;
such runs are marked budget-exhausted. Use provider-side spending controls separately.

There are no automatic API retries. An HTTP/transport failure can leave billed usage
unknown; aggregate tokens are then null, not zero. Do not run cost comparisons until
usage and infrastructure validity are complete.

The Docker daemon and image are checked before any model call. Images are resolved to
immutable local image IDs. A missing image is not silently downloaded during a trial.
The included configuration must be updated with real pinned model IDs before use.

## Limitations of the tested environment

The delivered selftest used trusted local fixtures only. API behavior was exercised with
mocked responses; Docker command construction, preflight and cleanup were mocked. A real
Docker + real API smoke test remains necessary before interpreting live runs. Docker
limits are defense in depth, not a proof of isolation against arbitrary hostile code.

## v0.2 additions

Poly requests include `protocol: poly-jsonl`, `language`, and `runtime_contract`.
The frozen plan controls per-level budgets; config cannot silently override them.
The supplied model snapshot must match the API response snapshot when returned.
Native runtime probes compile and execute before any paid call; failed probes abort.
Only public starter files are provided: stage-conditioned controller fixtures are never
model inputs. Input/stdout contracts use actual native processes and compilers.
Backend release tests currently cover these probes on the trusted local host and mock
Docker preflight/flags; they do not establish successful real container execution.
