FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml uv.lock VERSION ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and data
COPY src/ src/
COPY data/ data/

# Install the project itself
RUN uv sync --frozen --no-dev

# Default to scheduler; override with fly.toml [processes]
CMD ["uv", "run", "scheduler"]
