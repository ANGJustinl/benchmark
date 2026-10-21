# Runtime setup

The imported image builder expects Linux amd64 with Node 22.16.0, TypeScript 5.8.3
and Go 1.23.2 preinstalled on PATH. Host reference tests need Python 3.11+ and these
native tools. Other versions/platforms are not the pinned reproduction target.

Run scripts/check_environment.py first. Docker integration also needs a local daemon
and a JSON mapping of python/typescript/go/state to immutable image IDs; state is
the trusted Python transfer runtime.

```bash
docker pull python:3.12.14-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
python scripts/build_runtime_images.py --out /tmp/cb-images.json
python scripts/reproduce.py --tier docker --images /tmp/cb-images.json --out /tmp/cb-docker-check
```

The builder copies preinstalled tools and uses --network=none --pull=false. It does
not install dependencies through networked RUN steps. Preserve platform proxy/CA
settings when preparing trusted tools; never disable TLS verification.

Candidates never pull images, enable networking or receive API credentials.
Per-process deadlines remain 8s execution, 120s compile and 30s startup in the
imported protocol; these are distinct from removed model-agent budgets.

Use containers/ with the builder above. Older examples/images templates are historical,
not the pinned recipe. Missing prerequisites cause explicit failure, not a skip or
host fallback. Docker integration must be explicitly invoked.
