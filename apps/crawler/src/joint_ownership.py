"""Read-only legacy admission against the native joint transition contract.

No allocator adoption, journal creation, projection repair or write grant lives
here. Supported ownership changes still require every writer cold under ADR006.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from urllib.parse import unquote, urlsplit

import asyncpg

B0_LUA_SHA256 = "7c3b67b6b9eefdcf0dc9ae01f62a4d45f6ce0f67f8fa551dfd484dd4ce6fb59b"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_COHORTS = {
    "c1": ["browser-use-careers"],
    "c2": ["browser-use-careers", "kandou-ai-careers"],
    "c3": ["browser-use-careers", "eclypsium-careers", "kandou-ai-careers"],
    "cdom": ["algorized-careers", "browser-use-careers", "bunq-careers"],
}
_SPEC_FIELDS = (
    "version",
    "transition_id",
    "source_revision",
    "previous_epoch",
    "previous_ordinary_plan_sha256",
    "prepared_plan_sha256",
    "previous_b0_receipt_sha256",
    "target_b0_manifest_sha256",
    "active_release_sha256",
    "target_release_sha256",
    "rollback_release_sha256",
    "cold_attestation_sha256",
)
_JOINT_LUA = (Path(__file__).parent / "lua/ordinary_joint_admission.lua").read_text()


def _reject() -> NoReturn:
    raise ValueError("joint ownership rejected")


def read_b0_audit(path: str) -> str:
    if not path:
        return ""
    if not os.path.isabs(path):
        _reject()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or not 0 < info.st_size <= 131072:
            _reject()
        body = handle.read(131073)
    if not 0 < len(body) <= 131072 or hashlib.sha256(body).hexdigest() != B0_LUA_SHA256:
        _reject()
    return body.decode("utf-8")


@dataclass(frozen=True)
class _Number:
    value: str


def _normalize_number(raw: str) -> str:
    negative = raw.startswith("-")
    if negative:
        raw = raw[1:]
    pieces = re.split("[eE]", raw)
    exponent = 0
    if len(pieces) == 2:
        # Match Go ParseInt's bounded arithmetic without converting coefficients
        # to binary floats or enormous Python integers.
        digits = pieces[1].lstrip("+-").lstrip("0")
        if len(digits) > 7:
            _reject()
        exponent = int(digits) if digits else 0
        if pieces[1].startswith("-"):
            exponent = -exponent
        if not -1000000 <= exponent <= 1000000:
            _reject()
    coefficient = pieces[0]
    if "." in coefficient:
        whole, fraction = coefficient.split(".")
        exponent -= len(fraction)
        coefficient = whole + fraction
    coefficient = coefficient.lstrip("0")
    if not coefficient:
        return "0"
    exponent += len(coefficient) - 1
    if not -1000000 <= exponent <= 1000000:
        _reject()
    coefficient = coefficient.rstrip("0")
    if -6 <= exponent < 21:
        position = exponent + 1
        if position <= 0:
            body = "0." + "0" * -position + coefficient
        elif position >= len(coefficient):
            body = coefficient + "0" * (position - len(coefficient))
        else:
            body = coefficient[:position] + "." + coefficient[position:]
    else:
        body = coefficient[0]
        if len(coefficient) > 1:
            body += "." + coefficient[1:]
        body += "e" + str(exponent)
    return "-" + body if negative else body


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            _reject()
        result[key] = value
    return result


def _constant(_: str) -> object:
    _reject()
    return None


def _go_json(value: object, *, sorted_keys: bool = False, depth: int = 0) -> str:
    if depth > 64:
        _reject()
    if isinstance(value, _Number):
        return _normalize_number(value.value)
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if isinstance(value, str):
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        # encoding/json's default string encoding, including HTML escaping.
        return (
            encoded.replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        )
    if isinstance(value, list):
        return (
            "["
            + ",".join(_go_json(v, sorted_keys=sorted_keys, depth=depth + 1) for v in value)
            + "]"
        )
    if isinstance(value, dict):
        keys = sorted(value) if sorted_keys else value
        return (
            "{"
            + ",".join(
                _go_json(k) + ":" + _go_json(value[k], sorted_keys=sorted_keys, depth=depth + 1)
                for k in keys
            )
            + "}"
        )
    _reject()
    return ""


def canonical_metadata(raw: str) -> str:
    if not isinstance(raw, str) or not 0 < len(raw.encode("utf-8")) <= 1048576:
        _reject()
    value = json.loads(
        raw,
        object_pairs_hook=_object,
        parse_int=_Number,
        parse_float=_Number,
        parse_constant=_constant,
    )
    if not isinstance(value, dict):
        _reject()
    result = _go_json(value, sorted_keys=True)
    result.encode("utf-8")
    return result


def _document(body: str, digest: str, maximum: int) -> dict:
    if (
        not isinstance(body, str)
        or not isinstance(digest, str)
        or not 0 < len(body.encode("utf-8")) <= maximum
        or not _SHA256.fullmatch(digest)
        or hashlib.sha256(body.encode("utf-8")).hexdigest() != digest
    ):
        _reject()
    value = json.loads(body, object_pairs_hook=_object, parse_constant=_constant)
    if not isinstance(value, dict) or _go_json(value) != body:
        _reject()
    return value


def _spec(body: str, digest: str, revision: str) -> dict:
    value = _document(body, digest, 4096)
    if (
        tuple(value) != _SPEC_FIELDS
        or value["version"] != "jobseek.crawler.cold-transition/v1"
        or not isinstance(value["transition_id"], str)
        or not _UUID.fullmatch(value["transition_id"])
        or value["source_revision"] != revision
        or not _REVISION.fullmatch(revision)
        or type(value["previous_epoch"]) is not int
        or not 1 <= value["previous_epoch"] < 9999999999999
    ):
        _reject()
    for key in _SPEC_FIELDS[4:]:
        digest_value = value[key]
        if (
            key in ("previous_ordinary_plan_sha256", "previous_b0_receipt_sha256")
            and digest_value == ""
        ):
            continue
        if not isinstance(digest_value, str) or not _SHA256.fullmatch(digest_value):
            _reject()
    return value


def _target(body: str, digest: str) -> dict:
    value = _document(body, digest, 16384)
    if (
        tuple(value) != ("version", "namespace", "shard_id", "cohort", "boards")
        or value["version"] != "jobseek.crawler.cold-b0-target/v1"
        or not isinstance(value["namespace"], str)
        or not _SAFE_ID.fullmatch(value["namespace"])
        or not isinstance(value["shard_id"], str)
        or not _SAFE_ID.fullmatch(value["shard_id"])
        or not isinstance(value["cohort"], str)
        or value["cohort"] not in _COHORTS
        or not isinstance(value["boards"], list)
    ):
        _reject()
    boards = value["boards"]
    if len(boards) != len(_COHORTS[value["cohort"]]):
        _reject()
    seen = set()
    for board, slug in zip(boards, _COHORTS[value["cohort"]], strict=True):
        if (
            not isinstance(board, dict)
            or tuple(board) != ("board_id", "board_slug", "config_sha256")
            or not isinstance(board["board_id"], str)
            or not _UUID.fullmatch(board["board_id"])
            or board["board_id"] in seen
            or board["board_slug"] != slug
            or not isinstance(board["config_sha256"], str)
            or not _SHA256.fullmatch(board["config_sha256"])
        ):
            _reject()
        seen.add(board["board_id"])
    return value


async def _no_open_joint(conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy) -> None:
    open_joint = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM public.crawler_ownership_transition "
        "WHERE phase NOT IN ('reversed','superseded'))"
    )
    if open_joint is not False:
        _reject()


async def attest_joint_ownership(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
    payload: str | None,
    plan: str,
    revision: str,
    epoch: str,
    lua: str,
) -> None:
    if payload is None:
        await _no_open_joint(conn)
        return
    journal = await conn.fetchrow(
        "SELECT intent_sha256,payload,phase FROM public.crawler_ownership_transition "
        "WHERE reserved_plan_sha256=$1 AND routing_epoch=$2 AND source_revision=$3",
        plan,
        int(epoch),
        revision,
    )
    if journal is None:
        await _no_open_joint(conn)
        return
    if journal["phase"] != "active" or hashlib.sha256(lua.encode()).hexdigest() != B0_LUA_SHA256:
        _reject()
    spec = _spec(journal["payload"], journal["intent_sha256"], revision)
    target_body = await conn.fetchval(
        "SELECT payload FROM public.crawler_ownership_b0_target WHERE target_sha256=$1",
        spec["target_b0_manifest_sha256"],
    )
    if not isinstance(target_body, str):
        _reject()
    target = _target(target_body, spec["target_b0_manifest_sha256"])
    ordinary = json.loads(payload, object_pairs_hook=_object, parse_constant=_constant)
    ordinary_ids = {m["board_id"] for m in ordinary["members"]}
    # Lazy import: redis_queue imports LegacyOwnership for the frozen claim ABI.
    from src.redis_queue import get_redis

    client = get_redis()
    for board in target["boards"]:
        if board["board_id"] in ordinary_ids:
            _reject()
        row = await conn.fetchrow(
            "SELECT id::text,company_id::text,board_url,crawler_type,"
            "COALESCE(metadata,'{}'::jsonb)::text AS metadata,"
            "check_interval_minutes::text,scrape_interval_hours::text,"
            "COALESCE(throttle_key,'') AS throttle_key,monitor_needs_browser,scraper_needs_browser "
            "FROM public.job_board WHERE board_slug=$1 AND is_enabled "
            "AND board_status='active' FOR SHARE",
            board["board_slug"],
        )
        if (
            row is None
            or row["id"] != board["board_id"]
            or row["scraper_needs_browser"] is not True
        ):
            _reject()
        parsed = urlsplit(row["board_url"])
        if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None:
            _reject()
        # Go URL.Hostname preserves case and decodes escaped IPv6 zones.
        host = parsed.netloc
        if host.startswith("["):
            host = unquote(host[1 : host.index("]")])
        elif ":" in host:
            host = host.rsplit(":", 1)[0]
        canonical = {
            k: row[k]
            for k in (
                "company_id",
                "board_url",
                "crawler_type",
                "check_interval_minutes",
                "scrape_interval_hours",
                "throttle_key",
            )
        }
        canonical.update(
            board_slug=board["board_slug"],
            metadata=canonical_metadata(row["metadata"]),
            domain=row["throttle_key"] or host,
            monitor_needs_browser="1" if row["monitor_needs_browser"] else "0",
            scraper_needs_browser="1",
        )
        cached = await client.hgetall("board:" + board["board_id"])
        cached_metadata = cached.get("metadata", "")
        if not isinstance(cached_metadata, str):
            _reject()
        cached["metadata"] = canonical_metadata(cached_metadata)
        if any(cached.get(k) != v for k, v in canonical.items()):
            _reject()
        if (
            hashlib.sha256(_go_json(canonical, sorted_keys=True).encode()).hexdigest()
            != board["config_sha256"]
        ):
            _reject()
    prefix = "lightpanda-b0:{" + target["namespace"] + "}:"
    keys = [
        prefix + k
        for k in ("route", "records", "ready", "inflight", "dead", "terminal", "origin-holders")
    ]
    keys += ["ordinary:ownership:active", "crawler:ownership:transition"]
    marker = _go_json(
        {
            "version": "jobseek.crawler.cold-publication/v1",
            "state": "published",
            "intent_sha256": journal["intent_sha256"],
            "source_revision": revision,
            "routing_epoch": int(epoch),
            "plan_sha256": plan,
            "b0_target_sha256": spec["target_b0_manifest_sha256"],
        }
    )
    args = [
        "audit",
        target["shard_id"],
        epoch,
        "go",
        "",
        "0",
        "",
        "0",
        "0",
        "0",
        "",
        "",
        "",
        "64",
        "2.0",
        "",
        "0",
        target["namespace"],
        "",
        "0",
        target["cohort"],
        str(len(target["boards"])),
        "0",
    ]
    args += [b["board_slug"] for b in target["boards"]] + [payload, marker]
    script = "local function audited_b0()\n" + lua + "\nend\n" + _JOINT_LUA
    if await client.eval(script, len(keys), *keys, *args) != "accepted":
        _reject()
