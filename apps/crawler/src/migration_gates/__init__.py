"""Offline crawler migration promotion and reversal gates."""

from __future__ import annotations

from src.migration_gates.model import GateModelError, evaluate_promotion

__all__ = ["GateModelError", "evaluate_promotion"]
