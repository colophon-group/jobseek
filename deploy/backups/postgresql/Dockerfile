ARG POSTGRES_IMAGE=postgres:16-alpine@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416
FROM ${POSTGRES_IMAGE} AS pgbackrest-build

ARG PGBACKREST_VERSION=2.59.0
ARG PGBACKREST_SHA256=faaf8faa14a6392279654ee216a493fcd07b0c513af4b55fe34faec062cb8875

RUN apk add --no-cache \
      build-base \
      bzip2-dev \
      curl \
      libxml2-dev \
      lz4-dev \
      meson \
      ninja \
      openssl-dev \
      pkgconf \
      postgresql-dev \
      python3 \
      zlib-dev \
      zstd-dev \
    && curl --fail --location --silent --show-error \
      "https://github.com/pgbackrest/pgbackrest/releases/download/release/${PGBACKREST_VERSION}/pgbackrest-${PGBACKREST_VERSION}.tar.gz" \
      --output /tmp/pgbackrest.tar.gz \
    && echo "${PGBACKREST_SHA256}  /tmp/pgbackrest.tar.gz" | sha256sum -c - \
    && tar --extract --gzip --file /tmp/pgbackrest.tar.gz --directory /tmp \
    && cd "/tmp/pgbackrest-${PGBACKREST_VERSION}" \
    && meson setup build . \
      --buildtype=release \
      -Dlibssh2=disabled \
      -Dlibzstd=enabled \
      -Dlibsystemd=disabled \
    && ninja -C build \
    && meson test -C build --suite smoke --print-errorlogs \
    && install -o root -g root -m 0755 build/src/pgbackrest /usr/local/bin/pgbackrest

FROM ${POSTGRES_IMAGE}

ARG PGBACKREST_VERSION=2.59.0

RUN apk add --no-cache \
      libbz2 \
      libxml2 \
      lz4-libs \
      zlib \
      zstd-libs

COPY --from=pgbackrest-build /usr/local/bin/pgbackrest /usr/local/bin/pgbackrest

RUN test "$(pgbackrest version)" = "pgBackRest ${PGBACKREST_VERSION}"
