# ── Stage 1: Slim base (Python + deps, no Playwright) ────────────────
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install Python dependencies (cached layer)
COPY pyproject.toml uv.lock VERSION ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and data
COPY src/ src/
COPY data/ data/

# Install the project itself
RUN uv sync --frozen --no-dev

# ── Target: slim (worker, exporter, drain — no Playwright) ───────────
FROM base AS slim
# Default: worker instance. Override via docker-compose command.
CMD ["uv", "run", "--no-sync", "crawler", "run"]

# ── Target: full (browser worker — with Playwright + Chromium) ───────
FROM base AS full

# OS deps for headless Chromium
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 \
       libcairo2 libcups2t64 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0t64 \
       libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 \
       libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
       libfontconfig1 libfreetype6 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Download Chromium binary
#
# Install the browser by its explicit Playwright name (``chromium-headless-shell``)
# rather than ``--only-shell chromium``. Both produce the same on-disk layout
# (``.../chromium_headless_shell-<rev>/chrome-headless-shell-linux64/...``) but
# the explicit form has been more reliable across Playwright 1.54→1.58 when
# Docker layer caches are reused — see issue #2213, where the browser binary
# was missing at runtime despite the build logs claiming success.
#
# The post-install ``test`` fails the build loudly if the expected binary
# isn't on disk; without this guard, the defect only surfaces after the image
# is deployed and the first ``browser_worker`` task tries to launch.
RUN uv run --no-sync playwright install chromium-headless-shell \
    && test -x /root/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell

CMD ["uv", "run", "--no-sync", "crawler", "run-browser"]
