"""Explicit allowlisted producer for the exclusive Go-owned B0 lane."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from redis.asyncio import Redis

from src.config import settings
from src.lightpanda.routing import resolve_render_assignment
from src.lightpanda_queue import (
    MAX_INTEGER,
    POLICY_KEY,
    Decision,
    LightpandaB0Queue,
    LightpandaB0Task,
    RouteIdentity,
)
from src.runtime.config import BoardRuntimeConfig


class LightpandaB0ProducerError(RuntimeError):
    """An allowlisted task could not be exclusively registered in B0."""


_COHORTS = {
    "c1": frozenset({"browser-use-careers"}),
    "c4": frozenset(
        {
            "browser-use-careers",
            "eclypsium-careers",
            "kandou-ai-careers",
            "poke-and-wiggle-careers",
        }
    ),
}


def _allowlist() -> frozenset[str]:
    cohort = settings.lightpanda_b0_producer_cohort
    if cohort == "off":
        raise LightpandaB0ProducerError("enabled B0 producer requires cohort c1 or c4")
    try:
        return _COHORTS[cohort]
    except KeyError as exc:
        raise LightpandaB0ProducerError(
            "LIGHTPANDA_B0_PRODUCER_COHORT must be off, c1, or c4"
        ) from exc


async def enqueue_if_allowlisted(
    redis: Redis,
    *,
    domain: str,
    posting_id: str,
    next_scrape_at: float,
    config: Mapping[str, Any],
    browser: bool,
    operator_transfer: bool = False,
) -> bool | None:
    """Return ``None`` for the legacy cohort, otherwise own the enqueue."""

    task = await build_allowlisted_task(
        redis,
        domain=domain,
        posting_id=posting_id,
        next_scrape_at=next_scrape_at,
        config=config,
        browser=browser,
    )
    if task is None:
        return None
    queue = LightpandaB0Queue(redis, namespace=settings.lightpanda_b0_queue_namespace)
    initialized = await queue.initialize(task.route)
    if not initialized.accepted:
        raise LightpandaB0ProducerError(
            f"B0 initialize failed: {initialized.decision.value}/{initialized.reason}"
        )
    registered = await queue.activate_legacy(
        task, legacy_config=config, operator_transfer=operator_transfer
    )
    if registered.accepted:
        return registered.reason in {"activated", "reactivated"}
    if registered.decision is not Decision.NOT_CURRENT or registered.reason not in {
        "task_already_exists",
        "state_mismatch",
    }:
        raise LightpandaB0ProducerError(
            f"B0 register failed: {registered.decision.value}/{registered.reason}"
        )

    stored = await queue.inspect(posting_id, task.route)
    if stored is None:
        raise LightpandaB0ProducerError("B0 record disappeared after duplicate registration")
    current, wanted = stored.task, task
    if (
        current.board_id != wanted.board_id
        or current.source_url != wanted.source_url
        or current.domain != wanted.domain
        or current.assignment != wanted.assignment
    ):
        raise LightpandaB0ProducerError("B0 task identity changed without a route migration")
    if stored.state in {"ready", "inflight"}:
        repaired = await queue.activate_legacy(
            current, legacy_config=config, operator_transfer=operator_transfer
        )
        if not repaired.accepted:
            raise LightpandaB0ProducerError(
                f"B0 residual repair failed: {repaired.decision.value}/{repaired.reason}"
            )
        return False
    if current.config_revision >= MAX_INTEGER:
        raise LightpandaB0ProducerError("B0 config revision is exhausted")
    replacement = LightpandaB0Task.create(
        task_id=posting_id,
        board_id=wanted.board_id,
        source_url=wanted.source_url,
        policy_key=POLICY_KEY,
        domain=wanted.domain,
        route=wanted.route,
        config_revision=current.config_revision + 1,
        initial_ready_at_ms=wanted.initial_ready_at_ms,
        assignment=wanted.assignment,
    )
    reactivated = await queue.activate_legacy(
        replacement,
        legacy_config=config,
        previous_payload_sha256=current.payload_sha256,
        operator_transfer=operator_transfer,
    )
    if not reactivated.accepted:
        raise LightpandaB0ProducerError(
            f"B0 reactivate failed: {reactivated.decision.value}/{reactivated.reason}"
        )
    return True


async def build_allowlisted_task(
    redis: Redis,
    *,
    domain: str,
    posting_id: str,
    next_scrape_at: float,
    config: Mapping[str, Any],
    browser: bool,
) -> LightpandaB0Task | None:
    """Validate and freeze one task without mutating queue state."""

    mode = settings.lightpanda_b0_producer_mode
    if mode == "off":
        return None
    if mode != "enabled":
        raise LightpandaB0ProducerError("LIGHTPANDA_B0_PRODUCER_MODE must be off or enabled")
    board_id = config.get("board_id")
    if not isinstance(board_id, str) or not board_id:
        raise LightpandaB0ProducerError("B0 scrape has no board UUID")
    snapshot = BoardRuntimeConfig.from_mapping(await redis.hgetall(f"board:{board_id}"))
    if not snapshot.board_slug:
        raise LightpandaB0ProducerError("B0 producer could not resolve the board slug")
    if snapshot.board_slug not in _allowlist():
        return None
    if not browser or config.get("scrape_step", "0") != "0":
        raise LightpandaB0ProducerError("allowlisted B0 work must be browser scraper step zero")
    scraper_type = snapshot.metadata.get("scraper_type", "json-ld")
    parser_config = snapshot.scraper_config
    if scraper_type != "json-ld" or parser_config is None:
        raise LightpandaB0ProducerError("allowlisted board has no JSON-LD parser assignment")
    try:
        assignment = resolve_render_assignment("json-ld", parser_config, scraper_step=0)
    except ValueError as exc:
        raise LightpandaB0ProducerError("allowlisted board assignment is invalid") from exc
    if assignment is None:
        raise LightpandaB0ProducerError("allowlisted board has no explicit Lightpanda assignment")

    route = RouteIdentity(
        shard_id=settings.lightpanda_b0_shard_id,
        routing_epoch=int(settings.lightpanda_b0_routing_epoch),
        engine_owner="go",
    )
    ready_at_ms = int(next_scrape_at * 1000)
    return LightpandaB0Task.create(
        task_id=posting_id,
        board_id=board_id,
        source_url=str(config.get("source_url", "")),
        policy_key=POLICY_KEY,
        domain=domain,
        route=route,
        config_revision=1,
        initial_ready_at_ms=ready_at_ms,
        assignment=assignment,
    )
