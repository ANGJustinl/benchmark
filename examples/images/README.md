# Runtime preparation (not exercised in this delivery)

Build/pull images before the measured experiment. Use a trusted, digest-pinned base.
Python image: Python 3.11+ stdlib. Go image: Go 1.23+ toolchain and stdlib. TypeScript image:
Node 22+ plus the TypeScript compiler. `Dockerfile.typescript` is a template, not a
prebuilt or security-reviewed image. No Docker command was run in the delivery environment.

Resolve and record local image IDs with `docker image inspect --format '{{.Id}}' IMAGE`.
Put IDs in `examples/live-config.json`; changing an image requires a new experiment.
Do not put API keys, the benchmark controller, hidden tests or gold implementations in
runtime images. Candidate mounts contain only its source, compiled output and its private
state directory. Disable network, drop capabilities and keep the root filesystem read-only.

The host reference runner uses a local trusted Go cache to avoid repeatedly rebuilding the
stdlib. Live Docker builds use fresh temporary compiler caches. Compile times are recorded
separately and are not cross-language scores. Wall-time estimates need dedicated, stable
hardware and repeat measurements; a memory/CPU cap alone does not guarantee exclusive resources.
