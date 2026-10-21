FROM python:3.12.14-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
COPY --chmod=0755 tools/go /usr/local/go
ENV PATH="/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin"
USER 65534:65534
WORKDIR /src
