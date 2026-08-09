"""Bounded confirmation policy for provider-native board-gone signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

GONE_CONFIRMATION_SPACING = timedelta(hours=6)
GONE_RECENT_SUCCESS_WINDOW = timedelta(days=7)
GONE_RECOVERY_INTERVAL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class GoneConfirmationDecision:
    """The next durable state after one explicit provider-gone signal."""

    board_status: str
    confirmation_count: int
    required_confirmations: int
    confirmation_advanced: bool
    terminal_transition: bool
    first_confirmed_at: datetime
    last_confirmed_at: datetime
    gone_at: datetime | None
    next_check_at: datetime


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def evaluate_gone_confirmation(
    *,
    board_status: str,
    confirmation_count: int,
    first_confirmed_at: datetime | None,
    last_confirmed_at: datetime | None,
    last_success_at: datetime | None,
    gone_at: datetime | None,
    now: datetime | None = None,
) -> GoneConfirmationDecision:
    """Apply spacing, recent-success gating, and terminal recovery cadence.

    A recently healthy board needs three provider-native gone confirmations;
    other boards need two. Confirmations must be six hours apart. Even a
    confirmed-gone configured board remains scheduled once per day so the same
    provider token can prove that it exists again without an operator write.
    """

    current = _aware(now) or datetime.now(UTC)
    first = _aware(first_confirmed_at)
    last = _aware(last_confirmed_at)
    success = _aware(last_success_at)
    prior_gone_at = _aware(gone_at)
    count = max(0, confirmation_count)
    recent_success = success is not None and success >= current - GONE_RECENT_SUCCESS_WINDOW
    required = 3 if recent_success else 2

    if board_status == "gone":
        advanced = last is None or current - last >= GONE_RECOVERY_INTERVAL
        confirmation_time = current if advanced else (last or current)
        next_check = confirmation_time + GONE_RECOVERY_INTERVAL
        return GoneConfirmationDecision(
            board_status="gone",
            confirmation_count=count,
            required_confirmations=required,
            confirmation_advanced=advanced,
            terminal_transition=False,
            first_confirmed_at=first or confirmation_time,
            last_confirmed_at=confirmation_time,
            gone_at=prior_gone_at or confirmation_time,
            next_check_at=next_check,
        )

    # A successful recovery resets ``confirmation_count`` while retaining the
    # last episode's timestamps as forensic evidence. Count zero therefore
    # always starts a fresh episode, even if the old timestamp is recent.
    advanced = count == 0 or last is None or current - last >= GONE_CONFIRMATION_SPACING
    next_count = count + int(advanced)
    confirmation_time = current if advanced else (last or current)
    terminal = next_count >= required
    return GoneConfirmationDecision(
        board_status="gone" if terminal else "gone_pending",
        confirmation_count=next_count,
        required_confirmations=required,
        confirmation_advanced=advanced,
        terminal_transition=terminal,
        first_confirmed_at=(current if advanced and count == 0 else first) or confirmation_time,
        last_confirmed_at=confirmation_time,
        gone_at=current if terminal else None,
        next_check_at=confirmation_time
        + (GONE_RECOVERY_INTERVAL if terminal else GONE_CONFIRMATION_SPACING),
    )


__all__ = [
    "GONE_CONFIRMATION_SPACING",
    "GONE_RECENT_SUCCESS_WINDOW",
    "GONE_RECOVERY_INTERVAL",
    "GoneConfirmationDecision",
    "evaluate_gone_confirmation",
]
