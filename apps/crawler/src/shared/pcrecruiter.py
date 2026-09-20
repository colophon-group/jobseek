"""Strict identity helpers for PCRecruiter hosted job boards."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlencode, urlparse

_HOST = "host.pcrecruiter.net"
_PATH = "/pcrbin/jobboard.aspx"
_UID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}")
_RECORD_ID_RE = re.compile(r"[0-9]{1,32}")


@dataclass(frozen=True, slots=True)
class PCRecruiterBoard:
    uid: str

    @property
    def listing_url(self) -> str:
        return f"https://{_HOST}{_PATH}?uid={quote(self.uid, safe='.')}"

    @property
    def post_url(self) -> str:
        return f"https://{_HOST}{_PATH}"

    def job_url(self, record_id: str) -> str:
        if _RECORD_ID_RE.fullmatch(record_id) is None:
            raise ValueError("PCRecruiter record ID must contain only digits")
        query = urlencode(
            {"uid": self.uid, "action": "detail", "recordid": record_id}
        )
        return f"https://{_HOST}{_PATH}?{query}"


def normalize_pcrecruiter_uid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    uid = value.strip()
    return uid if _UID_RE.fullmatch(uid) is not None else None


def pcrecruiter_board_from_url(url: str) -> PCRecruiterBoard | None:
    """Parse one exact, unfiltered public PCRecruiter board URL."""
    try:
        parsed = urlparse(url)
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold().rstrip(".") != _HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path.casefold() != _PATH.casefold()
        or parsed.params
        or parsed.fragment
        or len(query) != len({key.casefold() for key, _value in query})
    ):
        return None

    normalized_query = {key.casefold(): value for key, value in query}
    if set(normalized_query) not in ({"uid"}, {"uid", "filter"}):
        return None
    if normalized_query.get("filter", ""):
        return None
    uid = normalize_pcrecruiter_uid(normalized_query.get("uid"))
    return PCRecruiterBoard(uid) if uid is not None else None


def pcrecruiter_board_from_metadata(metadata: object) -> PCRecruiterBoard | None:
    if not isinstance(metadata, dict) or set(metadata) - {"uid"}:
        return None
    uid = normalize_pcrecruiter_uid(metadata.get("uid"))
    return PCRecruiterBoard(uid) if uid is not None else None
