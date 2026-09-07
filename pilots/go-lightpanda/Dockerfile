FROM golang:1.24-bookworm AS go-source

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./

FROM go-source AS build

RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w" -o /out/go-lightpanda .

FROM go-source AS integration-build

RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go test -c -o /out/go-lightpanda.test .

FROM debian:bookworm-slim AS lightpanda

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && curl --fail --location --retry 3 --output /usr/local/bin/lightpanda \
        https://github.com/lightpanda-io/browser/releases/download/0.4.0/lightpanda-x86_64-linux \
    && echo "bfcf9bd7e80939b87232aa114a49d8f397f51af0c2632d9fc58d4a6d4386624f  /usr/local/bin/lightpanda" \
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
