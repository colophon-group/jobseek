"""Exact legacy owner binding for the supported all-writer ordinary cutover.

This module cannot stage, activate, adopt the allocator or repair Redis state.
Every installed legacy claim reattests its startup identity under the same DB
lease/epoch barriers used by native claims and ownership transitions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

import asyncpg

from src.config import settings

LEASE_BARRIER = 7544422533504811010
EPOCH_BARRIER = 7544422533504811009


class OrdinaryOwnershipError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("ordinary ownership rejected")


@dataclass(frozen=True)
class LegacyOwnership:
    plan_sha256: str
    projection_sha1: str
    source_revision: str
    routing_epoch: str

    def __post_init__(self) -> None:
        if (
            re.fullmatch(r"[0-9a-f]{64}", self.plan_sha256) is None
            or re.fullmatch(r"[0-9a-f]{40}", self.projection_sha1) is None
            or re.fullmatch(r"[0-9a-f]{40}", self.source_revision) is None
            or re.fullmatch(r"[1-9][0-9]{0,12}", self.routing_epoch) is None
        ):
            raise OrdinaryOwnershipError()

    def claim_arguments(self) -> tuple[str, ...]:
        return (
            "",  # Tokenless legacy attempt; native claims carry a private token.
            "legacy",
            self.plan_sha256,
            self.projection_sha1,
            self.routing_epoch,
            self.source_revision,
            "",
            "",
        )


def configured_legacy_ownership() -> LegacyOwnership | None:
    values = (
        settings.ordinary_ownership_plan_sha256,
        settings.ordinary_ownership_projection_sha1,
        settings.ordinary_ownership_source_revision,
        settings.ordinary_ownership_routing_epoch,
    )
    if not any(values):
        return None
    return LegacyOwnership(*values)


def _independent_detail_domain_matches(detail: dict) -> bool:
    if detail["profile"] != "notion.public-detail/v1":
        return detail["domain"] == "*"
    config = detail.get("config")
    if not isinstance(config, dict) or config.get("crawler_type") != "notion":
        return False
    board_url = config.get("board_url")
    if (
        not isinstance(board_url, str)
        or re.fullmatch(r"[\w-]{1,128}\.notion\.site", detail["domain"]) is None
    ):
        return False
    try:
        board = urlsplit(board_url)
        return (
            board.scheme == "https"
            and board.netloc == detail["domain"]
            and board.username is None
            and board.password is None
            and board.port is None
        )
    except ValueError:
        return False


@lru_cache(maxsize=1)
def ownership_projection(payload: str) -> str:
    """Derive the exact Go routing projection from the immutable SQL payload."""
    plan = json.loads(payload)
    if plan["version"] != "jobseek.ordinary.ownership/v1":
        raise OrdinaryOwnershipError()
    if "routing_projection" not in plan:
        return payload
    if plan["routing_projection"] != "jobseek.ordinary.ownership-projection/v1":
        raise OrdinaryOwnershipError()
    members = {m["board_id"]: m["domain"] for m in plan["members"]}
    if (
        not 1 <= len(members) <= 20000
        or len(members) != len(plan["members"])
        or any(
            not isinstance(board, str) or not isinstance(domain, str)
            for board, domain in members.items()
        )
        or any(
            m["worker"]
            != (
                "browser"
                if m["profile"]
                in {
                    "dom.rendered-urls/v1",
                    "dom.rendered-rows/v1",
                    "inline.rendered-items/v1",
                    "rss.rendered-generic-skip/v1",
                    "rss.rendered-generic-items/v1",
                    "rss.rendered-generic-summary-skip/v1",
                    "rss.rendered-generic-summary-items/v1",
                    "rss.rendered-wp_job_manager-skip/v1",
                    "rss.rendered-wp_job_manager-items/v1",
                    "dayforce.session-search/v1",
                    "nextdata.rendered-items/v1",
                    "nextdata.rendered-urls/v1",
                    "api_sniffer.browser-items/v1",
                    "darwinbox.session-items/v1",
                    "bytedance.partition-items/v1",
                    "accenture.http-items/v1",
                }
                else "simple"
            )
            for m in plan["members"]
        )
    ):
        raise OrdinaryOwnershipError()
    document = {
        "version": "jobseek.ordinary.ownership-projection/v1",
        "routing_epoch": plan["routing_epoch"],
        "source_revision": plan["source_revision"],
        "plan_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "members": dict(sorted(members.items())),
    }
    if "details" in plan:
        details = {d["board_id"]: d["domain"] for d in plan["details"]}
        if (
            not 1 <= len(details) <= 20000
            or len(details) != len(plan["details"])
            or any(
                not isinstance(board, str) or not isinstance(domain, str) or not domain
                for board, domain in details.items()
            )
            or any(
                d["worker"]
                != (
                    "browser"
                    if d["profile"]
                    in (
                        "dom.rendered-detail/v1",
                        "jsonld.rendered-detail/v1",
                        "embedded.rendered-detail/v1",
                    )
                    else "simple"
                )
                or d["profile"]
                not in (
                    "workday.cxs-detail/v1",
                    "jsonld.direct-detail/v1",
                    "smartrecruiters.api-detail/v1",
                    "workable.api-detail/v1",
                    "workable.proxy-api-detail/v1",
                    "join.nextdata-detail/v1",
                    "dom.direct-detail/v1",
                    "dom.rendered-detail/v1",
                    "jsonld.rendered-detail/v1",
                    "oracle_hcm.api-detail/v1",
                    "embedded.direct-detail/v1",
                    "embedded.rendered-detail/v1",
                    "api_sniffer.http-detail/v1",
                    "mokahr.encrypted-detail/v1",
                    "eightfold.jsonld-api-detail/v1",
                    "adp.public-detail/v1",
                    "paylocity.html-detail/v1",
                    "paycom.public-detail/v1",
                    "rippling.v1-detail/v1",
                    "paylocity.proxy-html-detail/v1",
                    "eightfold.proxy-jsonld-api-detail/v1",
                    "dom.proxy-detail/v1",
                    "jsonld.proxy-detail/v1",
                    "api_sniffer.proxy-http-detail/v1",
                    "notion.public-detail/v1",
                    "pdf.public-detail/v1",
                    "seek.graphql-detail/v1",
                    "jobstreet.graphql-detail/v1",
                    "linkedin.guest-detail/v1",
                    "jazzhr.public-detail/v1",
                    "taleo.enterprise-detail/v1",
                    "jobconvo.public-detail/v1",
                    "johdi.api-detail/v1",
                    "headhunter.api-detail/v1",
                    "headhunter.proxy-api-detail/v1",
                    "infor.session-detail/v1",
                    "peoplesoft.session-detail/v1",
                )
                or (d["profile"] == "workday.cxs-detail/v1" and d["board_id"] not in members)
                or (
                    d["profile"] != "workday.cxs-detail/v1"
                    and (
                        not _independent_detail_domain_matches(d)
                        or not isinstance(d.get("company_id"), str)
                        or re.fullmatch(
                            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", d["company_id"]
                        )
                        is None
                        or not isinstance(d.get("effective_config_sha256"), str)
                        or re.fullmatch(r"[0-9a-f]{64}", d["effective_config_sha256"]) is None
                        or not isinstance(d.get("config"), dict)
                    )
                )
                for d in plan["details"]
            )
        ):
            raise OrdinaryOwnershipError()
        document["details"] = dict(sorted(details.items()))
    projection = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # Match encoding/json's default HTML and JavaScript line-separator escaping.
    for character in ("<", ">", "&", "\u2028", "\u2029"):
        projection = projection.replace(character, "\\u" + format(ord(character), "04x"))
    return projection


# One immutable, fully attested startup payload observation. Fresh write checks
# still bind active SQL identity and allocator under both barriers; no per-write
# transfer or decoding of the full fleet document is needed.
_detail_ownership: tuple[LegacyOwnership, frozenset[str]] | None = None


async def _attest(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy, expected: LegacyOwnership | None
) -> str | None:
    row = await conn.fetchrow(
        "SELECT plan_sha256,routing_epoch,source_revision,payload "
        "FROM public.ordinary_worker_ownership_plan WHERE state='active'"
    )
    if expected is None:
        if row is not None:
            raise OrdinaryOwnershipError()
        return
    if row is None:
        raise OrdinaryOwnershipError()
    allocator = await conn.fetchrow(
        "SELECT last_value,is_called FROM public.lightpanda_b0_routing_epoch_seq"
    )
    payload = row["payload"]
    if (
        row["plan_sha256"] != expected.plan_sha256
        or str(row["routing_epoch"]) != expected.routing_epoch
        or row["source_revision"] != expected.source_revision
        or allocator is None
        or allocator["is_called"] is not True
        or str(allocator["last_value"]) != expected.routing_epoch
        or not isinstance(payload, str)
        or not 1 <= len(payload.encode("utf-8")) <= 16777216
        or hashlib.sha256(payload.encode("utf-8")).hexdigest() != expected.plan_sha256
        or hashlib.sha1(ownership_projection(payload).encode("utf-8")).hexdigest()
        != expected.projection_sha1
    ):
        raise OrdinaryOwnershipError()
    global _detail_ownership
    _detail_ownership = (expected, _detail_boards(payload))
    return payload


async def _require_active_identity(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy, expected: LegacyOwnership
) -> None:
    # The transition trigger retains active/retired plans and forbids payload
    # changes. Startup attests the payload; each claim/write still checks its
    # exact current SQL identity and allocator while holding both barriers.
    active = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan p "
        "CROSS JOIN public.lightpanda_b0_routing_epoch_seq e "
        "WHERE p.state='active' AND p.plan_sha256=$1 AND p.source_revision=$2 "
        "AND p.routing_epoch=$3 AND e.is_called AND e.last_value=$3)",
        expected.plan_sha256,
        expected.source_revision,
        int(expected.routing_epoch),
    )
    if active is not True:
        raise OrdinaryOwnershipError()


class OrdinaryDetailWriteRejected(asyncio.CancelledError):
    """Ownership loss cancels an old detail without spending its failure budget."""

    def __init__(self) -> None:
        super().__init__("ordinary detail write rejected")


@lru_cache(maxsize=1)
def _detail_boards(payload: str) -> frozenset[str]:
    return frozenset(d["board_id"] for d in json.loads(payload).get("details", []))


async def require_legacy_detail_write(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy, posting_id: str
) -> None:
    """Bind the actual SQL posting to its board inside the write transaction.

    The caller holds this connection's transaction through the write. Cached
    scrape.board_id is never authority for excluding a canonical native detail.
    """
    try:
        await conn.execute("SELECT pg_advisory_xact_lock_shared($1)", LEASE_BARRIER)
        await conn.execute("SELECT pg_advisory_xact_lock_shared($1)", EPOCH_BARRIER)
        expected = configured_legacy_ownership()
        if expected is None:
            await _attest(conn, None)
            return
        selected = _detail_ownership
        if selected is None or selected[0] != expected:
            await _attest(conn, expected)
            selected = _detail_ownership
        if selected is None or selected[0] != expected:
            raise OrdinaryDetailWriteRejected()
        await _require_active_identity(conn, expected)
        boards = selected[1]
        if not boards:
            return
        row = await conn.fetchrow(
            "SELECT p.board_id::text AS board_id FROM public.job_posting p "
            "JOIN public.job_board b ON b.id=p.board_id "
            "WHERE p.id=$1::uuid FOR UPDATE OF b,p",
            posting_id,
        )
        if row is None or row["board_id"] in boards:
            raise OrdinaryDetailWriteRejected()
    except asyncio.CancelledError:
        raise
    except Exception:
        raise OrdinaryDetailWriteRejected() from None


@asynccontextmanager
async def legacy_ownership_barrier(
    pool: asyncpg.Pool, expected: LegacyOwnership | None, *, attest_payload: bool = False
) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(15), pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock_shared($1)", LEASE_BARRIER)
            await conn.execute("SELECT pg_advisory_xact_lock_shared($1)", EPOCH_BARRIER)
            selected = _detail_ownership
            if attest_payload or expected is None or selected is None or selected[0] != expected:
                await _attest(conn, expected)
            else:
                await _require_active_identity(conn, expected)
            yield
    except asyncio.CancelledError:
        raise
    except Exception:
        # Neither DB errors nor installed config values are diagnostic authority.
        raise OrdinaryOwnershipError() from None


async def prepare_legacy_ownership(pool: asyncpg.Pool) -> LegacyOwnership | None:
    try:
        expected = configured_legacy_ownership()
    except Exception:
        raise OrdinaryOwnershipError() from None
    async with legacy_ownership_barrier(pool, expected, attest_payload=True):
        pass
    return expected
