ARG GO_IMAGE=golang:1.24.7-alpine3.22@sha256:fc2cff6625f3c1c92e6c85938ac5bd09034ad0d4bc2dfb08278020b68540dbb5
FROM ${GO_IMAGE} AS go-source

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

LABEL org.jobseek.lightpanda.release="nightly-2026-09-28"
COPY lightpanda-release.json /usr/local/share/jobseek/lightpanda-release.json

ARG TARGETARCH
RUN case "${TARGETARCH}" in \
        amd64) lightpanda_asset=594332787; lightpanda_sha=e7dfca7686ad4ca5831b5cd741684b2dde6579c9f6046a848206cf5ad39efd3f ;; \
        arm64) lightpanda_asset=594332898; lightpanda_sha=e5e3b57fb1c99325c1b66e5f1e25d02199f21d116126a74296578bcc0ae9cd8f ;; \
        *) echo "unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && curl --fail --location --retry 3 --output /usr/local/bin/lightpanda \
        --header "Accept: application/octet-stream" \
        --header "X-GitHub-Api-Version: 2022-11-28" \
        "https://api.github.com/repos/lightpanda-io/browser/releases/assets/${lightpanda_asset}" \
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
    -test.run '^(TestLightpandaIntegration|TestLightpandaNavigationReadinessIntegration|TestLightpandaRuntimeV1BridgeIntegration|TestLightpandaRuntimeV1StdioIntegration|TestLightpandaPoolRuntimeV1IntegrationC4|TestLightpandaEgressPolicyIntegration)$' \
    -test.count=1 -test.v

FROM pilot-base AS runtime

COPY --from=build /out/go-lightpanda /usr/local/bin/go-lightpanda

ENTRYPOINT ["/usr/local/bin/go-lightpanda"]

FROM pilot-base AS service

ARG SOURCE_COMMIT
RUN test "${#SOURCE_COMMIT}" -eq 40 \
    && case "${SOURCE_COMMIT}" in ''|*[!0-9a-f]*) exit 1 ;; esac
LABEL org.jobseek.lightpanda.source-commit="${SOURCE_COMMIT}"

COPY --from=build /out/go-lightpanda /usr/local/bin/go-lightpanda

USER 0:0
ENTRYPOINT ["/usr/bin/setpriv", "--reuid=10001", "--regid=10001", "--clear-groups", "--inh-caps=+kill,+setgid,+setuid", "--ambient-caps=+kill,+setgid,+setuid", "--nnp", "--", "/usr/local/bin/go-lightpanda"]
CMD ["--runtime-v1-service"]

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
