ARG POSTGRES_IMAGE=postgres:16-alpine@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416
FROM ${POSTGRES_IMAGE}

RUN apk add --no-cache \
      openssh-client \
      pgbackrest=2.57.0-r0 \
    && pgbackrest version
