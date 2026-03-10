FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install OS deps for Playwright headless Chromium (render-based scrapers).
# Minimal set only — no LLVM/Mesa/xvfb/CJK fonts — saves ~700MB vs --with-deps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 \
       libcairo2 libcups2t64 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0t64 \
       libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 \
       libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
       libfontconfig1 libfreetype6 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml uv.lock VERSION ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and data
COPY src/ src/
COPY data/ data/

# Install the project itself, then download the matching browser binary
RUN uv sync --frozen --no-dev
RUN uv run playwright install --only-shell chromium

# Default to scheduler; override with fly.toml [processes]
CMD ["uv", "run", "scheduler"]
