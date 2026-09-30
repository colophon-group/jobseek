"""Resident Go enrichment and HTML normalization for monitors and details."""

from __future__ import annotations

import atexit
import json
import math
import os
import select
import subprocess
import threading
import time
from contextlib import suppress
from pathlib import Path

from src.metrics import runtime_execution_duration_seconds, runtime_executions_total
from src.shared.constants import get_data_dir
from src.shared.egress import record_runtime_capability

_MAX_REQUEST = (16 << 20) - 1
_MAX_RESPONSE = 1 << 20
# HTML serialization can expand text delimiters up to fivefold. Go emits UTF-8
# and does not JSON-escape HTML delimiters. Bound this operation separately;
# the taxonomy/experience response bound and handshake stay at 1 MiB.
_MAX_HTML_RESPONSE = 6 * _MAX_REQUEST + 1024


class GoJobEnrichment:
    def __init__(self, binary: str, data_dir: Path, *, timeout: float = 10):
        self.binary = binary
        self.data_dir = data_dir
        self.timeout = timeout
        self.lock = threading.Lock()
        self.proc = None
        self.owner_pid = os.getpid()
        self.sequence = 0
        self.buffer = bytearray()

    def _wait(self, fd: int, deadline: float, *, writing: bool = False) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Go enrichment response deadline exceeded")
        read, write, _ = select.select(
            [] if writing else [fd], [fd] if writing else [], [], remaining
        )
        if not read and not write:
            raise TimeoutError("Go enrichment response deadline exceeded")

    def _read(self, deadline: float, *, limit: int = _MAX_RESPONSE) -> dict:
        assert self.proc is not None and self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        while b"\n" not in self.buffer:
            self._wait(fd, deadline)
            chunk = os.read(fd, min(65536, limit + 1 - len(self.buffer)))
            if not chunk:
                raise RuntimeError("Go enrichment exited before its response")
            self.buffer.extend(chunk)
            if len(self.buffer) > limit:
                raise ValueError("Go enrichment response exceeds bound")
        line, _, remainder = self.buffer.partition(b"\n")
        self.buffer = bytearray(remainder)
        result = json.loads(line)
        if not isinstance(result, dict):
            raise ValueError("invalid Go enrichment response")
        return result

    def _close(self) -> None:
        proc, self.proc = self.proc, None
        self.buffer.clear()
        if proc is None:
            return
        if proc.stdin:
            proc.stdin.close()
        if self.owner_pid == os.getpid():
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        proc.kill()
                    proc.wait(timeout=2)
        if proc.stdout:
            proc.stdout.close()

    def close(self) -> None:
        with self.lock:
            self._close()

    def after_fork(self) -> None:
        # The child owns copies of pipe FDs, never the parent's process.
        self._close()
        self.owner_pid = os.getpid()
        self.lock = threading.Lock()

    def request(self, operation: str, **fields) -> dict:
        with self.lock:
            started = time.monotonic()
            outcome = "error"
            try:
                deadline = time.monotonic() + self.timeout
                if self.proc is None:
                    self.proc = subprocess.Popen(
                        [self.binary, "--data-dir", str(self.data_dir)],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        bufsize=0,
                    )
                    assert self.proc.stdin is not None and self.proc.stdout is not None
                    os.set_blocking(self.proc.stdin.fileno(), False)
                    os.set_blocking(self.proc.stdout.fileno(), False)
                    if self._read(deadline) != {"ready": True, "protocol": 1}:
                        raise ValueError("unsupported Go enrichment handshake")
                self.sequence += 1
                payload = (
                    json.dumps({"id": self.sequence, "operation": operation, **fields}).encode()
                    + b"\n"
                )
                if len(payload) > _MAX_REQUEST:
                    raise ValueError("Go enrichment request exceeds bound")
                assert self.proc.stdin is not None
                fd = self.proc.stdin.fileno()
                offset = 0
                while offset < len(payload):
                    self._wait(fd, deadline, writing=True)
                    offset += os.write(fd, payload[offset : offset + 65536])
                result = self._read(
                    deadline,
                    limit=_MAX_HTML_RESPONSE if operation == "normalize_html" else _MAX_RESPONSE,
                )
                if type(result.get("id")) is not int or result["id"] != self.sequence:
                    raise ValueError("Go enrichment response ID mismatch")
                if result.get("error"):
                    raise RuntimeError("Go enrichment rejected the request")
                outcome = "success"
                return result
            except BaseException:
                self._close()
                raise
            finally:
                runtime_execution_duration_seconds.labels(
                    stage="enrichment", implementation="go-job-enrichment"
                ).observe(time.monotonic() - started)
                runtime_executions_total.labels(
                    stage="enrichment", implementation="go-job-enrichment", outcome=outcome
                ).inc()
                record_runtime_capability(
                    stage="enrichment",
                    implementation="go-job-enrichment",
                    capability=operation,
                    allowed_capabilities=frozenset(
                        {
                            "occupation_seniority",
                            "technology",
                            "experience",
                            "salary",
                            "normalize_html",
                            "language",
                            "all_languages",
                            "location_resolve",
                            "location_ancestors",
                            "location_display",
                        }
                    ),
                    outcome=outcome,
                )


_client: GoJobEnrichment | None = None
_client_lock = threading.Lock()


def _after_fork() -> None:
    global _client_lock
    _client_lock = threading.Lock()
    if _client is not None:
        _client.after_fork()


os.register_at_fork(after_in_child=_after_fork)


def close_client() -> None:
    if _client is not None:
        _client.close()


atexit.register(close_client)


def enabled() -> bool:
    value = os.environ.get("JOB_ENRICHMENT_ENGINE", "python")
    if value not in {"go", "python"}:
        raise ValueError("JOB_ENRICHMENT_ENGINE must be go or python")
    return value == "go"


def client() -> GoJobEnrichment:
    global _client
    with _client_lock:
        if _client is None:
            _client = GoJobEnrichment("/usr/local/bin/job-enrichment", get_data_dir())
        return _client


def occupation_seniority(titles, occ_ids, sen_ids, employment_type=None):
    if isinstance(titles, str):
        titles = [titles]
    prepared = [
        title.strip() for title in (titles or []) if isinstance(title, str) and title.strip()
    ]
    result = client().request(
        "occupation_seniority", titles=prepared, employment_type=employment_type or ""
    )
    values = result.get("titles", [])
    if (
        not isinstance(values, list)
        or len(values) != len(prepared)
        or type(result.get("intern")) is not bool
    ):
        raise ValueError("invalid Go title classification")
    occ_id = sen_id = None
    for item in values:
        if not isinstance(item, dict) or any(
            v is not None and not isinstance(v, str) for v in item.values()
        ):
            raise ValueError("invalid Go taxonomy slug")
        if occ_id is None:
            occ_id = occ_ids.get(item.get("occupation"))
        if sen_id is None:
            sen_id = sen_ids.get(item.get("seniority"))
    if result["intern"]:
        sen_id = sen_ids.get("intern")
    return occ_id, sen_id


def technology_ids(description, tech_ids):
    if not description:
        return None
    result = client().request("technology", description=description)
    slugs = result.get("technologies")
    if not isinstance(slugs, list) or not all(isinstance(s, str) for s in slugs):
        raise ValueError("invalid Go technology slugs")
    return sorted({tech_ids[s] for s in slugs if s in tech_ids}) or None


def experience_fields(description):
    if not description:
        return None, None
    result = client().request("experience", description=description)
    values = result.get("experience_min"), result.get("experience_max")
    if any(
        v is not None and (type(v) not in {int, float} or not math.isfinite(v) or not 0 <= v <= 30)
        for v in values
    ) or (values[1] is not None and (values[0] is None or values[1] < values[0])):
        raise ValueError("invalid Go experience requirement")
    return values


def normalize_html(description: str | None) -> str | None:
    if description is None:
        return None
    result = client().request("normalize_html", description=description)
    if "normalized_html" not in result:
        raise ValueError("missing Go normalized HTML")
    value = result["normalized_html"]
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError("invalid Go normalized HTML")
    return value


def detect_language(description: str) -> str | None:
    result = client().request("language", description=description)
    if "language" not in result:
        raise ValueError("missing Go language")
    value = result["language"]
    if value is not None and (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isalpha()
        or not value.islower()
    ):
        raise ValueError("invalid Go language")
    return value


def detect_all_languages(description: str) -> list[str]:
    result = client().request("all_languages", description=description)
    values = result.get("languages")
    if (
        not isinstance(values, list)
        or any(
            not isinstance(v, str) or not v.isascii() or not v.isalpha() or not v.islower()
            for v in values
        )
        or len(values) != len(set(values))
    ):
        raise ValueError("invalid Go languages")
    return values


_SALARY_CURRENCIES = frozenset(
    {
        "USD",
        "CAD",
        "EUR",
        "GBP",
        "CHF",
        "PLN",
        "CZK",
        "SEK",
        "DKK",
        "HUF",
        "RON",
        "BGN",
        "AUD",
        "NZD",
        "SGD",
        "HKD",
        "BRL",
        "MXN",
    }
)
_SALARY_PERIODS = {"yearly": "year", "monthly": "month", "hourly": "hour"}


def _salary_range_valid(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"min", "max", "currency", "period"}
        and type(value["min"]) is int
        and 0 <= value["min"] < 2**63
        and (value["max"] is None or (type(value["max"]) is int and 0 <= value["max"] < 2**63))
        and isinstance(value["currency"], str)
        and value["currency"] in _SALARY_CURRENCIES
        and isinstance(value["period"], str)
        and value["period"] in _SALARY_PERIODS
    )


def salary_result(description: str, rates: dict[str, float] | None = None) -> dict:
    result = client().request("salary", description=description, salary_rates=rates or {})
    value = result.get("salary")
    if not isinstance(value, dict) or set(value) != {"ranges", "unified", "parsed", "eur"}:
        raise ValueError("invalid Go salary result")
    ranges, unified, parsed, eur = (value[k] for k in ("ranges", "unified", "parsed", "eur"))
    if (
        not isinstance(ranges, list)
        or not all(_salary_range_valid(r) for r in ranges)
        or (unified is not None and not _salary_range_valid(unified))
        or bool(ranges) != (unified is not None)
        or (eur is not None and (type(eur) is not int or not 0 <= eur < 2**63))
    ):
        raise ValueError("invalid Go salary ranges")
    if unified is None:
        if parsed is not None or eur is not None:
            raise ValueError("unexpected Go salary fields")
    elif (
        not isinstance(parsed, dict)
        or set(parsed) != {"min", "max", "currency", "unit"}
        or parsed["currency"] != unified["currency"]
        or parsed["unit"] != _SALARY_PERIODS[unified["period"]]
        or any(
            v is not None and (type(v) not in {int, float} or not math.isfinite(v) or v < 0)
            for v in (parsed["min"], parsed["max"])
        )
        or parsed["min"] is None
        or (parsed["max"] is None) != (unified["max"] is None)
    ):
        raise ValueError("invalid Go parsed salary")
    return value


def salary_fields(description: str | None, rates: dict[str, float]):
    if not description:
        return None, None, None, None, None
    result = salary_result(description, rates)
    sr = result["unified"]
    if sr is None:
        return None, None, None, None, None
    return sr["min"], sr["max"], sr["currency"], sr["period"], result["eur"]
