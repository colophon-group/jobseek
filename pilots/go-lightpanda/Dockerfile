ARG LIGHTPANDA_IMAGE=lightpanda/browser@sha256:5b84708cb3d9bef841aba4a4cd299f4de0609ac1bd7d4c6fbfcbf168d56b685e
ARG GO_IMAGE=golang:1.26.4-alpine3.22@sha256:727cfc3c40be55cd1bc9a4a059406b28a059857e3be752aa9d09531e12c20c56
FROM ${GO_IMAGE} AS go-source

# The primary build context remains this pilot directory. The caller supplies
# contracts plus the four explicit pure Go library contexts below;
# the repository root and crawler credentials never enter the build context.
WORKDIR /workspace/pilots/go-lightpanda
COPY --from=contracts go.mod go.sum /workspace/apps/crawler/contracts/
COPY --from=contracts v1/gen/go/ /workspace/apps/crawler/contracts/v1/gen/go/
COPY --from=contracts v1/framing/ /workspace/apps/crawler/contracts/v1/framing/
COPY --from=contracts v1/lightpandaadapter/ /workspace/apps/crawler/contracts/v1/lightpandaadapter/
COPY --from=contracts v1/resourcepolicy/ /workspace/apps/crawler/contracts/v1/resourcepolicy/
COPY --from=contracts v1/dayforcesession/ /workspace/apps/crawler/contracts/v1/dayforcesession/
COPY --from=contracts v1/lightpandaclient/ /workspace/apps/crawler/contracts/v1/lightpandaclient/
COPY --from=contracts v1/b0task/ /workspace/apps/crawler/contracts/v1/b0task/
COPY --from=api-sniffer-monitor go.mod go.sum *.go adp_employment_types.json adp_salary_units.json /workspace/apps/crawler/go/api-sniffer-monitor/
COPY --from=dom-detail go.mod go.sum *.go presets.json /workspace/apps/crawler/go/dom-detail/
COPY --from=jsonld-detail go.mod go.sum *.go /workspace/apps/crawler/go/jsonld-detail/
COPY --from=publisher-policy go.mod go.sum *.go /workspace/apps/crawler/go/publisher-policy/
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

FROM ${LIGHTPANDA_IMAGE} AS lightpanda-source

FROM debian:trixie-20260824-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132 AS lightpanda

LABEL org.jobseek.lightpanda.release="1.0.0"
COPY lightpanda-release.json /usr/local/share/jobseek/lightpanda-release.json
# Copy only the binary. The upstream image's entrypoint, user, libraries and
# network configuration never become the Jobseek runtime.
COPY --from=lightpanda-source /bin/lightpanda /usr/local/bin/lightpanda

ARG TARGETARCH
RUN case "${TARGETARCH}" in \
        amd64) lightpanda_sha=aa5a4b8ed53d1e38b3c73f5b2647d0a84a82e6744557f45f9a9c85858aa031c3 ;; \
        arm64) lightpanda_sha=69791924bcee43b13b224af4c845622c5fe66fdbc1b8143bfaa39ca8f85244f5 ;; \
        *) echo "unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && echo "${lightpanda_sha}  /usr/local/bin/lightpanda" \
        | sha256sum --check --strict \
    && chmod 0755 /usr/local/bin/lightpanda \
    && apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
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
