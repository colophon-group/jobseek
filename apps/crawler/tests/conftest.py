from __future__ import annotations

import os

# Set DATABASE_URL before any src module import to prevent config failures
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
