FROM golang:1.24-bookworm AS go-source

# The primary build context remains this pilot directory. The caller supplies
# apps/crawler/contracts as the narrowly scoped BuildKit context "contracts";
# the repository root and crawler credentials never enter the build context.
WORKDIR /workspace/pilots/go-lightpanda
COPY --from=contracts go.mod go.sum /workspace/apps/crawler/contracts/
COPY --from=contracts v1/gen/go/ /workspace/apps/crawler/contracts/v1/gen/go/
COPY --from=contracts v1/framing/ /workspace/apps/crawler/contracts/v1/framing/
COPY --from=contracts v1/lightpandaadapter/ /workspace/apps/crawler/contracts/v1/lightpandaadapter/
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
COPY density/workload.v1.json ./density/workload.v1.json

FROM go-source AS build

ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=linux GOARCH="${TARGETARCH}" go build -trimpath -ldflags="-s -w" -o /out/go-lightpanda .

FROM go-source AS integration-build

ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=linux GOARCH="${TARGETARCH}" go test -c -o /out/go-lightpanda.test .

FROM go-source AS density-build

ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=linux GOARCH="${TARGETARCH}" go build \
    -tags densitybench -trimpath -ldflags="-s -w" \
    -o /out/go-lightpanda-density .

FROM debian:trixie-20260824-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132 AS lightpanda

ARG TARGETARCH
RUN case "${TARGETARCH}" in \
        amd64) lightpanda_arch=x86_64; lightpanda_sha=bfcf9bd7e80939b87232aa114a49d8f397f51af0c2632d9fc58d4a6d4386624f ;; \
        arm64) lightpanda_arch=aarch64; lightpanda_sha=5e3b54deed642ffeb2b8f24a1931e54c51161f44d9d728135da3d4863cb722fb ;; \
        *) echo "unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && curl --fail --location --retry 3 --output /usr/local/bin/lightpanda \
        "https://github.com/lightpanda-io/browser/releases/download/0.4.0/lightpanda-${lightpanda_arch}-linux" \
    && echo "${lightpanda_sha}  /usr/local/bin/lightpanda" \
        | sha256sum --check --strict \
    && chmod 0755 /usr/local/bin/lightpanda \
    && apt-get purge --yes --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

FROM lightpanda AS pilot-base

RUN useradd --create-home --uid 10001 pilot
USER 10001:10001

ENV LIGHTPANDA_BIN=/usr/local/bin/lightpanda

FROM pilot-base AS integration-test

COPY --from=integration-build /out/go-lightpanda.test /usr/local/bin/go-lightpanda.test
ENV LIGHTPANDA_INTEGRATION_BIN=/usr/local/bin/lightpanda
RUN --network=none /usr/local/bin/go-lightpanda.test \
    -test.run '^(TestLightpandaIntegration|TestLightpandaRuntimeV1BridgeIntegration|TestLightpandaRuntimeV1StdioIntegration|TestLightpandaPoolRuntimeV1IntegrationC4|TestLightpandaEgressPolicyIntegration)$' \
    -test.count=1 -test.v

FROM pilot-base AS runtime

COPY --from=build /out/go-lightpanda /usr/local/bin/go-lightpanda

ENTRYPOINT ["/usr/local/bin/go-lightpanda"]

FROM pilot-base AS service

COPY --from=build /out/go-lightpanda /usr/local/bin/go-lightpanda

ENTRYPOINT ["/usr/local/bin/go-lightpanda", "--runtime-v1-service"]

FROM pilot-base AS density

ARG SOURCE_COMMIT
RUN test "${#SOURCE_COMMIT}" -eq 40 \
    && case "${SOURCE_COMMIT}" in ''|*[!0-9a-f]*) exit 1 ;; esac
LABEL org.jobseek.density.source-commit="${SOURCE_COMMIT}"

USER 0:0
RUN install -d --owner=10001 --group=10001 --mode=0555 /density
COPY --from=density-build /out/go-lightpanda-density /usr/local/bin/go-lightpanda-density
COPY --chown=10001:10001 --chmod=0444 \
	density/workload.v1.json /density/workload.v1.json
USER 10001:10001
RUN test -r /density/workload.v1.json

ENTRYPOINT ["/usr/local/bin/go-lightpanda-density"]
