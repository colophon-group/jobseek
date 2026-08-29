# ── Stage 1: Slim base (Python + deps, no Playwright) ────────────────
FROM python:3.13.15-slim-trixie@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS base

COPY --from=ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc /uv /uvx /bin/

WORKDIR /app

# OCR is an opt-in fallback for job offers published as image-only PDFs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (cached layer)
COPY pyproject.toml uv.lock VERSION ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the application plus the runtime-v1 package inputs. The
# contract package is installed from the built wheel below; the build inputs
# are removed so image imports cannot fall back to repository-relative files.
COPY src/ src/
COPY data/ data/
COPY contracts/v1/python/ contracts/v1/python/
COPY contracts/v1/framing/__init__.py contracts/v1/framing/__init__.py
COPY contracts/v1/framing/codec.py contracts/v1/framing/codec.py
COPY contracts/v1/privacy_registry.json contracts/v1/privacy_registry.json

# Install the project itself as the exact wheel exercised by contract CI.
RUN uv build --wheel --out-dir /tmp/jobseek-wheel \
    && uv pip install --python .venv/bin/python --no-deps /tmp/jobseek-wheel/*.whl \
    && rm -rf /tmp/jobseek-wheel contracts

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
# the exact "XServer running" error #2431 was meant to prevent. Docker
# restarts retain /tmp in the container writable layer, so the entrypoint
# also removes display-99 artifacts only after proving no live server owns
# them (#6011).
COPY scripts/with-xvfb.sh /usr/local/bin/with-xvfb
RUN chmod 0755 /usr/local/bin/with-xvfb

ENTRYPOINT ["/usr/local/bin/with-xvfb"]
CMD ["uv", "run", "--no-sync", "crawler", "run-browser"]
