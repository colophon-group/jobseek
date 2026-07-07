# ── Stage 1: Slim base (Python + deps, no Playwright) ────────────────
FROM python:3.13-slim AS base

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

# OS deps for headless Chromium + Google Chrome + xvfb (for boards that
# require headful real-Chrome to pass Akamai / PerimeterX / DataDome
# bot managers — see src/shared/browser.py persistent_context path).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 \
       libcairo2 libcups2t64 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0t64 \
       libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 \
       libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
       libfontconfig1 libfreetype6 fonts-liberation \
       wget gnupg ca-certificates xvfb xauth x11-utils \
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

# Install Google Chrome stable for boards that need ``channel: "chrome"``
# (a real Chrome binary, not Chromium-headless-shell). Akamai Bot
# Manager fingerprints the client-hints (``sec-ch-ua``) and rejects
# Chromium-shell profiles; Chrome stable passes on a warmed persistent
# profile. Post-install test is the same guard as above.
RUN wget -q -O /usr/share/keyrings/google-chrome.asc \
       https://dl.google.com/linux/linux_signing_key.pub \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.asc] https://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/* \
    && test -x /usr/bin/google-chrome

# Xvfb virtual display — boards that need headful (Akamai-gated, passed
# ``headless: false`` in monitor_config) otherwise can't launch Chrome
# in the container. Manual Xvfb + exec instead of ``xvfb-run`` because
# xvfb-run buffers stdout until the child exits, which silences structlog
# progress lines for the whole crawl cycle. The entrypoint script starts
# Xvfb in the background, waits for the X server to actually respond to
# ``xdpyinfo`` (not a blind ``sleep 1``), and exec's the crawler so
# signals pass through correctly. If Xvfb dies before becoming ready,
# the entrypoint fails loudly rather than leaving DISPLAY=:99 pointing
# at a dead server — otherwise Playwright would crash per-cycle with
# the exact "XServer running" error #2431 was meant to prevent.
RUN printf '%s\n' \
    '#!/bin/sh' \
    'set -e' \
    'Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &' \
    'XVFB_PID=$!' \
    'export DISPLAY=:99' \
    'ready=0' \
    'for i in $(seq 1 50); do' \
    '    if xdpyinfo -display :99 >/dev/null 2>&1; then' \
    '        ready=1; break' \
    '    fi' \
    '    if ! kill -0 "$XVFB_PID" 2>/dev/null; then' \
    '        echo "with-xvfb: Xvfb :99 died before becoming ready" >&2' \
    '        exit 1' \
    '    fi' \
    '    sleep 0.2' \
    'done' \
    'if [ "$ready" != 1 ]; then' \
    '    echo "with-xvfb: X server :99 did not respond within 10s" >&2' \
    '    exit 1' \
    'fi' \
    'exec "$@"' \
    > /usr/local/bin/with-xvfb \
    && chmod +x /usr/local/bin/with-xvfb

ENTRYPOINT ["/usr/local/bin/with-xvfb"]
CMD ["uv", "run", "--no-sync", "crawler", "run-browser"]
