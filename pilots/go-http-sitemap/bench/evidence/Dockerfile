FROM ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc AS uv

FROM python:3.13.15-slim-trixie@sha256:7ce4b6dfe35e55397b7cda544f8a13f191b7ae28dc5aad71fe664dbc9bc2623f

ARG EVIDENCE_COMMIT
ARG GO_ARCHIVE=go1.24.0.linux-amd64.tar.gz
ARG GO_ARCHIVE_SHA256=dea9ca38a0b852a74e81c26134671af7c0fbe65d81b0dc1c5bfe22cf7d4c8858

COPY --from=uv /uv /uvx /bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && curl --fail --location --proto '=https' --tlsv1.2 \
       --output "/tmp/${GO_ARCHIVE}" "https://go.dev/dl/${GO_ARCHIVE}" \
    && printf '%s  %s\n' "${GO_ARCHIVE_SHA256}" "/tmp/${GO_ARCHIVE}" | sha256sum --check --strict - \
    && tar -C /usr/local -xzf "/tmp/${GO_ARCHIVE}" \
    && rm -f "/tmp/${GO_ARCHIVE}" \
    && rm -rf /var/lib/apt/lists/* \
    && test "$(/usr/local/go/bin/go version)" = 'go version go1.24.0 linux/amd64'

COPY pilots/go-http-sitemap/bench/pyproject.toml /opt/jobseek-bench-env/pyproject.toml
COPY pilots/go-http-sitemap/bench/uv.lock /opt/jobseek-bench-env/uv.lock
RUN UV_PROJECT_ENVIRONMENT=/opt/jobseek-bench-venv \
    uv sync --project /opt/jobseek-bench-env --frozen --no-dev --no-install-project \
    && /opt/jobseek-bench-venv/bin/python -c \
       'import sys; assert sys.version.split()[0] == "3.13.15", sys.version'

# Dockerfile.dockerignore is deny-all with explicit source-input exceptions.
# The context is a disposable, unauthenticated public sparse clone; no developer
# checkout, .env file, company data, or credential path is admitted.
COPY . /opt/jobseek-evidence/source
RUN printf '%s\n' "${EVIDENCE_COMMIT}" | grep -Eq '^[0-9a-f]{40}$' \
    && test "$(git -C /opt/jobseek-evidence/source rev-parse HEAD)" = "${EVIDENCE_COMMIT}" \
    && test -z "$(git -C /opt/jobseek-evidence/source status --porcelain --untracked-files=all)" \
    && test -z "$(git -C /opt/jobseek-evidence/source ls-files --others --ignored --exclude-standard)" \
    && mkdir -p /tmp/jobseek-evidence-go-home /tmp/jobseek-evidence-go-cache \
    && cd /opt/jobseek-evidence/source/pilots/go-http-sitemap \
    && env -i PATH=/usr/local/go/bin:/usr/bin:/bin \
       HOME=/tmp/jobseek-evidence-go-home LANG=C LC_ALL=C CGO_ENABLED=0 \
       GOTOOLCHAIN=local GOCACHE=/tmp/jobseek-evidence-go-cache GOFLAGS=-mod=readonly \
       /usr/local/go/bin/go build -trimpath -o /opt/jobseek-evidence/go-runner ./bench/go-runner \
    && /opt/jobseek-bench-venv/bin/python \
       /opt/jobseek-evidence/source/pilots/go-http-sitemap/bench/evidence/write_runtime_attestation.py \
       --corpus /opt/jobseek-evidence/source/pilots/go-http-sitemap/bench/corpus.json \
       --lock /opt/jobseek-evidence/source/pilots/go-http-sitemap/bench/uv.lock \
       --out /opt/jobseek-evidence/python-runtime.json \
    && chmod -R a-w /opt/jobseek-evidence/source /opt/jobseek-evidence/go-runner \
       /opt/jobseek-evidence/python-runtime.json

ENV PATH="/usr/local/go/bin:/opt/jobseek-bench-venv/bin:/usr/local/bin:/usr/bin:/bin" \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /opt/jobseek-evidence/source/pilots/go-http-sitemap/bench

LABEL org.opencontainers.image.base.name="python:3.13.15-slim-trixie@sha256:7ce4b6dfe35e55397b7cda544f8a13f191b7ae28dc5aad71fe664dbc9bc2623f" \
      org.opencontainers.image.description="Issue-7948 evidence runtime built from an allowlisted public sparse clone" \
      org.opencontainers.image.version="go1.24.0-python3.13.15" \
      org.jobseek.evidence.python-attestation="/opt/jobseek-evidence/python-runtime.json" \
      org.jobseek.evidence.go-binary="/opt/jobseek-evidence/go-runner" \
      org.jobseek.evidence.source-root="/opt/jobseek-evidence/source" \
      org.jobseek.evidence.commit="${EVIDENCE_COMMIT}"

ENTRYPOINT ["/opt/jobseek-bench-venv/bin/python", "/opt/jobseek-evidence/source/pilots/go-http-sitemap/bench/orchestrator.py"]
