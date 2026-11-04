ARG LIGHTPANDA_IMAGE=lightpanda/browser@sha256:ed80bcf2f771b0c7da1b2937bd6c6e75e1a0199c31453cf5fa00ef4e0be88e57
ARG GO_IMAGE=docker.io/library/golang:1.26.4-alpine3.22@sha256:727cfc3c40be55cd1bc9a4a059406b28a059857e3be752aa9d09531e12c20c56
FROM ${GO_IMAGE} AS go-source

# The primary build context remains the renderer directory. The caller supplies
# contracts plus the four explicit pure Go library contexts below;
# the repository root and crawler credentials never enter the build context.
WORKDIR /workspace/apps/lightpanda
COPY --from=contracts go.mod go.sum /workspace/apps/crawler/contracts/
COPY --from=contracts v1/gen/go/ /workspace/apps/crawler/contracts/v1/gen/go/
COPY --from=contracts v1/framing/ /workspace/apps/crawler/contracts/v1/framing/
COPY --from=contracts v1/lightpandaadapter/ /workspace/apps/crawler/contracts/v1/lightpandaadapter/
COPY --from=contracts v1/browserproxy/ /workspace/apps/crawler/contracts/v1/browserproxy/
COPY --from=contracts v1/resourcepolicy/ /workspace/apps/crawler/contracts/v1/resourcepolicy/
COPY --from=contracts v1/dayforcesession/ /workspace/apps/crawler/contracts/v1/dayforcesession/
COPY --from=contracts v1/feedsession/ /workspace/apps/crawler/contracts/v1/feedsession/
COPY --from=contracts v1/documentactions/ /workspace/apps/crawler/contracts/v1/documentactions/
COPY --from=contracts v1/apireplay/ /workspace/apps/crawler/contracts/v1/apireplay/
COPY --from=contracts v1/lightpandaclient/ /workspace/apps/crawler/contracts/v1/lightpandaclient/
COPY --from=contracts v1/b0task/ /workspace/apps/crawler/contracts/v1/b0task/
COPY --from=api-sniffer-monitor go.mod go.sum *.go adp_employment_types.json adp_salary_units.json unifr_sources.json linkedin_countries.json provider_location_types.json final_static_detail_rules.json /workspace/apps/crawler/go/api-sniffer-monitor/
COPY --from=dom-detail go.mod go.sum *.go presets.json /workspace/apps/crawler/go/dom-detail/
COPY --from=jsonld-detail go.mod go.sum *.go /workspace/apps/crawler/go/jsonld-detail/
COPY --from=publisher-policy go.mod go.sum *.go /workspace/apps/crawler/go/publisher-policy/
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./

FROM go-source AS build

ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=linux GOARCH="${TARGETARCH}" go build -trimpath -ldflags="-s -w" -o /out/go-lightpanda .

FROM go-source AS integration-build

ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=linux GOARCH="${TARGETARCH}" go test -c -o /out/go-lightpanda.test .

FROM docker.io/${LIGHTPANDA_IMAGE} AS lightpanda-source

FROM docker.io/library/debian:trixie-20260824-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132 AS lightpanda

LABEL org.jobseek.lightpanda.release="1.1.0-nightly.11582+592b76832"
COPY lightpanda-release.json /usr/local/share/jobseek/lightpanda-release.json
# Copy only the binary. The upstream image's entrypoint, user, libraries and
# network configuration never become the Jobseek runtime.
COPY --from=lightpanda-source /usr/bin/lightpanda /usr/local/bin/lightpanda

ARG TARGETARCH
RUN case "${TARGETARCH}" in \
        amd64) lightpanda_sha=71ec3bd077e29a13f14b76a97a4a19d1a0fab8153b9af9490d105db4a4ee5118 ;; \
        arm64) lightpanda_sha=4e1a6e0954c2843eb8ccb08e4a20766e5358bde43b4ca0e25803e4bc7acaf627 ;; \
        *) echo "unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && echo "${lightpanda_sha}  /usr/local/bin/lightpanda" \
        | sha256sum --check --strict \
    && chmod 0755 /usr/local/bin/lightpanda \
    && apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

FROM lightpanda AS renderer-base

RUN useradd --create-home --uid 10001 lightpanda
USER 10001:10001

ENV LIGHTPANDA_BIN=/usr/local/bin/lightpanda

FROM renderer-base AS integration-test

COPY --from=integration-build /out/go-lightpanda.test /usr/local/bin/go-lightpanda.test
WORKDIR /integration-fixtures
COPY --chown=10001:10001 --chmod=0444 testdata/python_wait_states.json ./testdata/python_wait_states.json
ENV LIGHTPANDA_INTEGRATION_BIN=/usr/local/bin/lightpanda
RUN --network=none /usr/local/bin/go-lightpanda.test \
    -test.run '^(TestLightpandaIntegration|TestLightpandaHeapLimitIntegration|TestLightpandaDayforceSessionIntegration|TestLightpandaAPIReplaySessionIntegration|TestLightpandaSingleDetailCaptureIntegration|TestLightpandaAPIRootArraySessionIntegration|TestLightpandaRotatingAPISessionIntegration|TestLightpandaNativeBrowserProviderSessionIntegration|TestLightpandaDocumentActionsIntegration|TestLargeDocumentPhysicalWireKeepsCompleteHTMLAndLegacyLimit|TestLightpandaDocumentInteractionsIntegration|TestLightpandaOriginalWaitStateFixtures|TestLightpandaNavigationReadinessIntegration|TestLightpandaDOMAutoResourceIntegration|TestLightpandaDOMSessionRendersRootFetchesRawTailAndRetainsCookies|TestLightpandaWarmupRetainsOnlyItsOwnSessionForAPIAndRSS|TestLightpandaRuntimeV1BridgeIntegration|TestLightpandaRuntimeV1StdioIntegration|TestLightpandaPoolRuntimeV1IntegrationC4|TestLightpandaEgressPolicyIntegration|TestPhysicalLightpandaConfiguredProxyAuthenticationAndOriginIsolation|TestPhysicalLightpandaConfiguredProxyHTTPSRejectsUntrustedOrigin|TestPinnedProxyChildExecPortIsolation)$' \
    -test.count=1 -test.v

FROM renderer-base AS runtime

COPY --from=build /out/go-lightpanda /usr/local/bin/go-lightpanda

ENTRYPOINT ["/usr/local/bin/go-lightpanda"]

FROM renderer-base AS service

ARG SOURCE_COMMIT
RUN test "${#SOURCE_COMMIT}" -eq 40 \
    && case "${SOURCE_COMMIT}" in ''|*[!0-9a-f]*) exit 1 ;; esac
LABEL org.jobseek.lightpanda.source-commit="${SOURCE_COMMIT}"

COPY --from=build /out/go-lightpanda /usr/local/bin/go-lightpanda

USER 0:0
ENTRYPOINT ["/usr/bin/setpriv", "--reuid=10001", "--regid=10001", "--clear-groups", "--inh-caps=+kill,+setgid,+setuid", "--ambient-caps=+kill,+setgid,+setuid", "--nnp", "--", "/usr/local/bin/go-lightpanda"]
CMD ["--runtime-v1-service"]
