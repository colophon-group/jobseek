"""Explicit upstream-family to native Jobseek compatibility registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class Compatibility:
    """How one upstream inventory family enters Jobseek.

    ``native`` entries have a dedicated Jobseek monitor. ``generic`` entries
    deliberately use Jobseek-owned reusable machinery. ``excluded`` entries
    are job marketplaces or other non-company inventories and therefore never
    become company-request candidates.
    """

    monitor_type: str | None
    kind: Literal["native", "generic", "excluded"] = "native"
    monitor_config_json: str | None = None
    seedable: bool = True
    reason: str | None = None

    @property
    def candidate_eligible(self) -> bool:
        return self.kind != "excluded"

    @property
    def monitor_config(self) -> dict[str, object] | None:
        if self.monitor_config_json is None:
            return None
        value = json.loads(self.monitor_config_json)
        if not isinstance(value, dict):  # pragma: no cover - constant invariant
            raise TypeError("monitor_config_json must encode an object")
        return value


def _native(monitor_type: str) -> Compatibility:
    return Compatibility(monitor_type=monitor_type)


def _generic(
    monitor_type: str,
    *,
    config: str | None = None,
    seedable: bool = True,
    reason: str | None = None,
) -> Compatibility:
    return Compatibility(
        monitor_type=monitor_type,
        kind="generic",
        monitor_config_json=config,
        seedable=seedable,
        reason=reason,
    )


def _excluded(reason: str) -> Compatibility:
    return Compatibility(
        monitor_type=None,
        kind="excluded",
        seedable=False,
        reason=reason,
    )


# Keep this exhaustive and boring. A family absent from this table is
# unsupported, quarantined from company issue creation, and gets exactly one
# family support issue. Aliases point at the canonical Jobseek monitor.
COMPATIBILITY: dict[str, Compatibility] = {
    "adp": _native("adp"),
    "ashby": _native("ashby"),
    "avature": _native("avature"),
    "bamboohr": _native("bamboohr"),
    "beisen": _native("beisen"),
    "beisen_legacy": _native("beisen"),
    "breezy": _native("breezy"),
    "bytedance": _generic(
        "api_sniffer",
        seedable=False,
        reason="First-party board; let ws derive and verify the API configuration.",
    ),
    "cornerstone": _native("cornerstone"),
    "darwinbox": _native("darwinbox"),
    "dayforce": _native("dayforce"),
    "eightfold": _native("eightfold"),
    "gem": _native("gem"),
    "greenhouse": _native("greenhouse"),
    "gupy": _native("gupy"),
    "herp": _native("herp"),
    "hrmos": _native("hrmos"),
    "icims": _native("icims"),
    "infojobs_es": _excluded("Job marketplace, not a company ATS tenant inventory."),
    "jazzhr": _native("jazzhr"),
    "jobbankca": _excluded("Government job marketplace, not a company ATS tenant inventory."),
    "jobs_cz": _excluded("Job marketplace, not a company ATS tenant inventory."),
    "jobvite": _native("jobvite"),
    "join_com": _native("join"),
    "keka": _native("keka"),
    "lever": _native("lever"),
    "mercor": _generic(
        "api_sniffer",
        seedable=False,
        reason="First-party board; let ws derive and verify the API configuration.",
    ),
    "moka": _native("mokahr"),
    "oracle": _native("oracle_hcm"),
    "pageup": _native("pageup"),
    "paycom": _native("paycom"),
    "paylocity": _native("paylocity"),
    "personio": _native("personio"),
    "phenom": _native("phenom"),
    "pinpoint": _native("pinpoint"),
    "recruitee": _native("recruitee"),
    "recruiterbox": _native("recruiterbox"),
    "rippling": _native("rippling"),
    "seek": _excluded("SEEK/JobStreet/JobsDB marketplaces, not company ATS tenants."),
    "smartrecruiters": _native("smartrecruiters"),
    "softgarden": _native("softgarden"),
    "successfactors": _generic(
        "rss",
        config='{"preset":"successfactors"}',
        reason="SuccessFactors is implemented as the shared RSS monitor preset.",
    ),
    "taleo": _native("taleo"),
    "teamtailor": _generic(
        "rss",
        config='{"preset":"teamtailor"}',
        reason="Teamtailor is implemented as the shared RSS monitor preset.",
    ),
    "ukg": _native("ukg"),
    "workable": _native("workable"),
    "workday": _native("workday"),
}


def compatibility_for(family: str) -> Compatibility | None:
    return COMPATIBILITY.get(family)
