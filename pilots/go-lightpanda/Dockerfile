FROM golang:1.24-bookworm AS go-source

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./

FROM go-source AS build

ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=linux GOARCH="${TARGETARCH}" go build -trimpath -ldflags="-s -w" -o /out/go-lightpanda .

FROM go-source AS integration-build

ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=linux GOARCH="${TARGETARCH}" go test -c -o /out/go-lightpanda.test .

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
    -test.run '^TestLightpandaIntegration$' -test.count=1 -test.v

FROM pilot-base AS runtime

COPY --from=build /out/go-lightpanda /usr/local/bin/go-lightpanda

ENTRYPOINT ["/usr/local/bin/go-lightpanda"]
