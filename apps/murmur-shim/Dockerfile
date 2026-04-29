# syntax=docker/dockerfile:1.7
#
# jobseek-murmur-shim — production image (linux/arm64).
#
# Source spec: colophon-group/jobseek#2774 (H2). Sibling pattern of
# colophon-group/murmur's Dockerfile (multi-stage Node-on-Alpine,
# Corepack-pinned pnpm, tini PID 1, non-root `node` user).
#
# ── Why this is multi-stage ────────────────────────────────────────
#
# Builder stage carries Corepack + pnpm + the entire workspace + dev
# deps so it can run `pnpm --filter @jobseek/murmur-shim build`. The
# build emits a Next.js `output: "standalone"` tree at
# `apps/murmur-shim/.next/standalone/` containing a self-contained
# `server.js` and a pruned `node_modules/` with only the runtime deps
# (no typescript, no eslint, no vitest). Runtime stage copies just
# that tree + the static assets and discards everything else.
#
# ── Why no Python in this image ────────────────────────────────────
#
# The shim's `_lib/invoke-lib.ts` spawns `python3` to dispatch into
# the crawler's `cli_shim`. Baking Python + Playwright + browser
# binaries into this image would cost ~1.5 GB and duplicate what the
# `apps/crawler` image already provides on the same Hetzner host.
# Instead, compose mounts the crawler venv from the host:
#
#     /opt/jobseek-crawler/.venv  →  /opt/jobseek-crawler-venv
#     /opt/jobseek-crawler/src    →  /opt/jobseek-crawler-src
#
# and passes the env vars `MURMUR_PY=/opt/jobseek-crawler-venv/bin/python3`
# and `MURMUR_CRAWLER_ROOT=/opt/jobseek-crawler-src` (read by
# `invoke-lib.ts` at call time). This image only needs to be able to
# `spawn()` that interpreter — no native deps.
#
# Note: jobseek#2774 originally wrote `VENV_PYTHON` and `PYTHONPATH`
# as the env names, but the actual code reads `MURMUR_PY` and
# `MURMUR_CRAWLER_ROOT` (`PYTHONPATH` is set to `MURMUR_CRAWLER_ROOT`
# automatically inside the spawn — the operator does not pass it).
# Compose (H3) wires the names that the code reads.
#
# ── Build context ──────────────────────────────────────────────────
#
# Build context MUST be the monorepo root, because the shim's
# `src/db/schema.ts` re-exports `apps/web/src/db/schema.ts` and the
# pnpm workspace install needs the root lockfile + workspace manifest.
# Invoke as:
#
#     docker buildx build \
#       --platform=linux/arm64 \
#       -f apps/murmur-shim/Dockerfile \
#       -t jobseek-murmur-shim:test \
#       .
#
# The colocated `Dockerfile.dockerignore` is honored by BuildKit when
# the `-f` flag points at this Dockerfile.
#
# ── Runtime invariants ─────────────────────────────────────────────
#
# - PID 1 is `tini`, which forwards SIGTERM and reaps zombies.
# - Process runs as `node` (uid 1000), which exists in node:22-alpine
#   by default. We never `chown -R node:node /app/.next`; the COPY
#   --chown does it in a single layer.
# - EXPOSE 8080. Compose binds it to 127.0.0.1 only; cloudflared is
#   the public ingress.
# - HEALTHCHECK hits `/health` (added by jobseek#2774 alongside this
#   Dockerfile) — see app/health/route.ts for the route, which is
#   intentionally bearer-free so the healthcheck and the cloudflared
#   origin-up probe both work without secrets.

ARG NODE_IMAGE=node:22-alpine
# pnpm@10.30.0 matches the root `package.json`'s `packageManager` field.
# Mismatched versions would emit a hard Corepack error rather than
# silent drift.
ARG PNPM_VERSION=10.30.0

# ───────────────────────── builder ──────────────────────────
FROM ${NODE_IMAGE} AS builder

# libc6-compat is needed for some npm postinstall scripts (sharp's
# native bindings, esbuild). We don't pull g++/make/python3 because
# the shim has no native deps — drizzle-orm and postgres are pure JS.
RUN apk add --no-cache libc6-compat

# Activate pnpm via Corepack in a globally-readable location so
# subsequent stages (and humans `docker exec`-ing) can use it.
ENV COREPACK_HOME=/opt/corepack
ARG PNPM_VERSION
RUN mkdir -p /opt/corepack \
 && corepack enable \
 && corepack prepare pnpm@${PNPM_VERSION} --activate

WORKDIR /app

# Copy lockfile + workspace manifests first so `pnpm install` can be
# cached independently of source changes. The workspace's package
# manifests are tiny; we copy the whole tree of them in one shot
# (any package.json under apps/* or packages/*).
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./

# Workspace member manifests required so `pnpm install` resolves the
# graph. Missing any of these would make pnpm refuse to install (the
# lockfile pins workspace-wide). The order tracks `pnpm-workspace.yaml`:
# `apps/*` (every apps/<name> with a package.json), then
# `apps/crawler/murmur`, then `packages/*`.
COPY apps/murmur-shim/package.json ./apps/murmur-shim/
COPY apps/web/package.json ./apps/web/
COPY apps/trace-viewer/package.json ./apps/trace-viewer/
COPY apps/crawler/murmur/package.json ./apps/crawler/murmur/
COPY packages/mcp-server/package.json ./packages/mcp-server/

# Frozen install: fail fast on lockfile drift. `--prefer-offline`
# reduces network round-trips on warm builds.
RUN pnpm install --frozen-lockfile --prefer-offline

# Copy the rest of the workspace. Dockerfile.dockerignore filters out
# .git, .next, coverage, *.test.ts, etc.
COPY apps/murmur-shim/ ./apps/murmur-shim/
# `apps/web/src/db/schema.ts` is re-exported by the shim's
# `src/db/schema.ts`; Next's standalone tracer follows the relative
# path at build time and inlines the table definitions into a server
# chunk. We only need that one file from web — copying it explicitly
# avoids dragging the rest of the web app into the build context.
COPY apps/web/src/db/schema.ts ./apps/web/src/db/schema.ts

# Build the standalone Next.js bundle.
RUN pnpm --filter @jobseek/murmur-shim build

# ───────────────────────── runtime ──────────────────────────
FROM ${NODE_IMAGE} AS runtime

# tini for proper PID 1 behaviour. wget is already present via
# busybox-extras (alpine ships busybox-wget); we use it in HEALTHCHECK
# below.
RUN apk add --no-cache tini

WORKDIR /app

# Copy ONLY the standalone build output + static assets. The standalone
# tree is structured as `<outputFileTracingRoot>/apps/murmur-shim/...`
# — i.e., it preserves the monorepo layout because the build set
# `outputFileTracingRoot` to the repo root. We mirror that layout in
# the image so `node apps/murmur-shim/server.js` finds its peers
# (the bundled `apps/murmur-shim/.next/server/chunks/...` files and
# the `node_modules/` at `/app/node_modules` that standalone's tracer
# pruned to runtime-only deps).
#
# `--chown=node:node` sets ownership in the same layer as the COPY,
# which is much cheaper than a follow-up `chown -R` (no duplicate
# inode layer).
COPY --from=builder --chown=node:node \
     /app/apps/murmur-shim/.next/standalone/ ./
# Static assets (chunks/, css/) live outside the standalone tree; Next
# expects them at `<app>/.next/static/`.
COPY --from=builder --chown=node:node \
     /app/apps/murmur-shim/.next/static/ ./apps/murmur-shim/.next/static/

USER node

ENV NODE_ENV=production
# `HOSTNAME=0.0.0.0` is required by Next's standalone server.js to
# bind to all interfaces; the default `localhost` only listens on the
# loopback inside the container, which the docker-proxy can't reach.
ENV HOSTNAME=0.0.0.0
ENV PORT=8080

# Sane defaults for the venv-mount paths. Compose may override these;
# a healthy local `docker run` (without compose) will get a
# `MURMUR_DB_DSN` is empty error before it ever tries to spawn python,
# so these defaults can stay even when the volumes aren't mounted.
ENV MURMUR_PY=/opt/jobseek-crawler-venv/bin/python3
ENV MURMUR_CRAWLER_ROOT=/opt/jobseek-crawler-src

EXPOSE 8080

# Image-level healthcheck. Compose's healthcheck will override this
# when launched via `docker compose up`; the duplicate exists so plain
# `docker run` smoke tests still surface health, and so that a host-
# orchestrated `docker ps` shows the container's true state.
#
# `wget -q -O- ... || exit 1` — busybox wget exits non-zero on any
# 4xx/5xx, which is what we want. The `-q` keeps stderr quiet so
# `docker logs` doesn't fill with health probe noise.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O- http://127.0.0.1:8080/health || exit 1

# tini handles signals; node runs the standalone server entrypoint.
# The entrypoint lives at `apps/murmur-shim/server.js` because the
# build's outputFileTracingRoot was the monorepo root.
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "apps/murmur-shim/server.js"]
