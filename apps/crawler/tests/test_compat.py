"""Sync test: ensure _compat mirrors the runtime monitor registry."""

from __future__ import annotations

from src.core.monitors import all_monitor_types as core_all
from src.core.monitors import api_monitor_types as core_api
from src.core.monitors import is_rich_monitor as core_is_rich
from src.workspace._compat import all_monitor_types as compat_all
from src.workspace._compat import api_monitor_types as compat_api
from src.workspace._compat import is_rich_monitor as compat_is_rich


def test_all_monitor_types_match():
    assert compat_all() == core_all(), (
        f"_compat.all_monitor_types() drifted from core: "
        f"missing={core_all() - compat_all()}, extra={compat_all() - core_all()}"
    )


def test_api_monitor_types_match():
    assert compat_api() == core_api(), (
        f"_compat.api_monitor_types() drifted from core: "
        f"missing={core_api() - compat_api()}, extra={compat_api() - core_api()}"
    )


def test_is_rich_monitor_consistency():
    for mtype in core_all():
        assert compat_is_rich(mtype) == core_is_rich(mtype), (
            f"is_rich_monitor({mtype!r}) disagrees: "
            f"compat={compat_is_rich(mtype)}, core={core_is_rich(mtype)}"
        )

    # Also test api_sniffer with fields config
    cfg = {"fields": {"title": "name"}}
    assert compat_is_rich("api_sniffer", cfg) == core_is_rich("api_sniffer", cfg)
    assert compat_is_rich("api_sniffer", {}) == core_is_rich("api_sniffer", {})
    assert compat_is_rich("api_sniffer", None) == core_is_rich("api_sniffer", None)
