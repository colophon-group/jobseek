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

# ── Target: http (slim, no Playwright) ───────────────────────────────
FROM base AS http
CMD ["uv", "run", "--no-sync", "scheduler", "--http-only"]

# ── Target: r2-drain (slim, no Playwright, no data/) ─────────────────
FROM base AS r2-drain
RUN rm -rf data/
CMD ["uv", "run", "--no-sync", "scheduler", "--r2-drain-only"]

# ── Target: browser (full, with Playwright + Chromium) ────────────────
FROM base AS browser

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
RUN uv run --no-sync playwright install --only-shell chromium

CMD ["uv", "run", "--no-sync", "scheduler", "--browser-only"]
