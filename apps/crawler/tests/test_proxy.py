"""Tests for src.shared.proxy — provider-based proxy layer."""

from __future__ import annotations

import pytest

from src.shared.proxy import (
    PoolProxyProvider,
    ProxyConfigurationError,
    ProxyPoolExhaustedError,
    StaticProxyProvider,
    get_provider,
    httpx_proxy_for,
    playwright_proxy_for,
    report_proxy_failure,
    report_proxy_success,
)


def _set_provider(
    monkeypatch,
    name: str,
    webshare: str = "",
    webshare_pool: tuple[str, ...] = (),
    canary_slot: int | None = None,
) -> None:
    from src import config
    from src.shared import proxy as proxy_module

    proxy_module._provider_for_values.cache_clear()
    monkeypatch.setattr(config.settings, "proxy_provider", name)
    monkeypatch.setattr(config.settings, "webshare_proxy_urls", list(webshare_pool))
    monkeypatch.setattr(config.settings, "webshare_proxy_url", webshare)
    monkeypatch.setattr(config.settings, "webshare_proxy_canary_slot", canary_slot)


class TestStaticProxyProvider:
    def test_returns_url(self):
        p = StaticProxyProvider("webshare", "http://u:p@host:1000")
        assert p.name == "webshare"
        assert p.proxy_url() == "http://u:p@host:1000"

    def test_empty_url_returns_none(self):
        p = StaticProxyProvider("webshare", "")
        assert p.proxy_url() is None


class TestPoolProxyProvider:
    def test_round_robins_once_per_selection(self):
        p = PoolProxyProvider(
            "webshare",
            (
                "http://u:p@p.webshare.io:10000",
                "http://u:p@p.webshare.io:10001",
            ),
        )

        assert [p.select(origin=None, transport="httpx").pool_slot for _ in range(5)] == [
            0,
            1,
            0,
            1,
            0,
        ]

    def test_rejects_empty_pool(self):
        with pytest.raises(ValueError, match="must not be empty"):
            PoolProxyProvider("webshare", ())

    def test_selection_repr_hides_credentials(self):
        p = PoolProxyProvider(
            "webshare",
            ("http://sensitive-user:sensitive-password@p.webshare.io:10000",),
        )

        rendered = repr(p.select(origin=None, transport="httpx"))

        assert "sensitive-user" not in rendered
        assert "sensitive-password" not in rendered
        assert "p.webshare.io" not in rendered

    def test_forced_canary_slot_preserves_same_egress(self):
        p = PoolProxyProvider(
            "webshare",
            (
                "http://u:p@p.webshare.io:10000",
                "http://u:p@p.webshare.io:10001",
            ),
            forced_slot=1,
        )

        assert [
            p.select(origin="target.example", transport="playwright").pool_slot for _ in range(3)
        ] == [1, 1, 1]

    def test_origin_block_is_scoped_to_target(self):
        p = PoolProxyProvider(
            "webshare",
            (
                "http://u:p@p.webshare.io:10000",
                "http://u:p@p.webshare.io:10001",
            ),
        )
        blocked = p.select(origin="blocked.example", transport="httpx")
        report_proxy_failure(blocked, origin="blocked.example", reason="origin_block")

        assert p.select(origin="blocked.example", transport="httpx").pool_slot == 1
        # The first endpoint remains eligible for unrelated origins.
        assert p.select(origin="healthy.example", transport="httpx").pool_slot == 0

    def test_global_failure_is_skipped_for_every_origin(self):
        p = PoolProxyProvider(
            "webshare",
            (
                "http://u:p@p.webshare.io:10000",
                "http://u:p@p.webshare.io:10001",
            ),
        )
        failed = p.select(origin="one.example", transport="httpx")
        report_proxy_failure(failed, origin="one.example", reason="proxy_auth")

        assert p.select(origin="two.example", transport="httpx").pool_slot == 1

    def test_transport_failures_across_origins_promote_to_global_quarantine(self):
        p = PoolProxyProvider(
            "webshare",
            (
                "http://u:p@p.webshare.io:10000",
                "http://u:p@p.webshare.io:10001",
            ),
            forced_slot=0,
        )

        for origin in ("one.example", "two.example", "three.example"):
            selection = p.select(origin=origin, transport="httpx")
            report_proxy_failure(
                selection,
                origin=origin,
                reason="origin_transport",
            )

        with pytest.raises(ProxyPoolExhaustedError):
            p.select(origin="four.example", transport="httpx")

    def test_exhaustion_half_open_and_recovery(self):
        now = [0.0]
        p = PoolProxyProvider(
            "webshare",
            (
                "http://u:p@p.webshare.io:10000",
                "http://u:p@p.webshare.io:10001",
            ),
            clock=lambda: now[0],
        )
        selections = [p.select(origin="blocked.example", transport="httpx") for _ in range(2)]
        for selection in selections:
            report_proxy_failure(
                selection,
                origin="blocked.example",
                reason="origin_block",
            )

        with pytest.raises(ProxyPoolExhaustedError, match="cooling down"):
            p.select(origin="blocked.example", transport="httpx")

        now[0] = 15 * 60
        first_probe = p.select(origin="blocked.example", transport="httpx")
        second_probe = p.select(origin="blocked.example", transport="httpx")
        assert {first_probe.pool_slot, second_probe.pool_slot} == {0, 1}
        assert first_probe.half_open is True
        assert second_probe.half_open is True
        with pytest.raises(ProxyPoolExhaustedError):
            p.select(origin="blocked.example", transport="httpx")

        report_proxy_success(first_probe, origin="blocked.example")
        recovered = p.select(origin="blocked.example", transport="httpx")
        assert recovered.pool_slot == first_probe.pool_slot
        assert recovered.half_open is False

    def test_stale_success_cannot_reopen_newer_quarantine(self):
        p = PoolProxyProvider(
            "webshare",
            ("http://u:p@p.webshare.io:10000",),
        )
        failed = p.select(origin="target.example", transport="httpx")
        stale = p.select(origin="target.example", transport="httpx")

        report_proxy_failure(failed, origin="target.example", reason="origin_block")
        report_proxy_success(stale, origin="target.example")

        with pytest.raises(ProxyPoolExhaustedError):
            p.select(origin="target.example", transport="httpx")

    def test_stale_success_cannot_reopen_newer_global_quarantine(self):
        p = PoolProxyProvider(
            "webshare",
            ("http://u:p@p.webshare.io:10000",),
        )
        failed = p.select(origin="one.example", transport="httpx")
        stale = p.select(origin="two.example", transport="httpx")

        report_proxy_failure(failed, origin="one.example", reason="proxy_auth")
        report_proxy_success(stale, origin="two.example")

        with pytest.raises(ProxyPoolExhaustedError):
            p.select(origin="three.example", transport="httpx")

    def test_cross_origin_success_recovers_selected_origin_probe(self):
        now = [0.0]
        p = PoolProxyProvider(
            "webshare",
            ("http://u:p@p.webshare.io:10000",),
            clock=lambda: now[0],
        )
        failed = p.select(origin="a.example", transport="httpx")
        report_proxy_failure(failed, origin="a.example", reason="origin_block")
        now[0] = 15 * 60
        probe = p.select(origin="a.example", transport="httpx")

        report_proxy_success(probe, origin="b.example")

        assert p.select(origin="a.example", transport="httpx").half_open is False

    def test_cross_origin_block_recovers_selected_origin_and_quarantines_final_origin(self):
        now = [0.0]
        p = PoolProxyProvider(
            "webshare",
            ("http://u:p@p.webshare.io:10000",),
            clock=lambda: now[0],
        )
        failed = p.select(origin="a.example", transport="httpx")
        report_proxy_failure(failed, origin="a.example", reason="origin_block")
        now[0] = 15 * 60
        probe = p.select(origin="a.example", transport="httpx")

        report_proxy_failure(probe, origin="b.example", reason="origin_block")

        assert p.select(origin="a.example", transport="httpx").half_open is False
        with pytest.raises(ProxyPoolExhaustedError):
            p.select(origin="b.example", transport="httpx")

    def test_cross_origin_failure_with_existing_final_state_releases_selected_probe(self):
        now = [0.0]
        p = PoolProxyProvider(
            "webshare",
            ("http://u:p@p.webshare.io:10000",),
            clock=lambda: now[0],
        )
        failed_b = p.select(origin="b.example", transport="httpx")
        report_proxy_failure(failed_b, origin="b.example", reason="origin_block")
        failed_a = p.select(origin="a.example", transport="httpx")
        report_proxy_failure(failed_a, origin="a.example", reason="origin_block")
        now[0] = 15 * 60
        probe = p.select(origin="a.example", transport="httpx")

        report_proxy_failure(probe, origin="b.example", reason="origin_block")

        assert p.select(origin="a.example", transport="httpx").half_open is False

    def test_stale_global_failure_releases_current_origin_probe(self):
        now = [0.0]
        p = PoolProxyProvider(
            "webshare",
            ("http://u:p@p.webshare.io:10000",),
            clock=lambda: now[0],
        )
        failed_a = p.select(origin="a.example", transport="httpx")
        report_proxy_failure(failed_a, origin="a.example", reason="origin_block")
        now[0] = 15 * 60
        origin_probe = p.select(origin="a.example", transport="httpx")
        concurrent = p.select(origin="other.example", transport="httpx")
        report_proxy_failure(concurrent, origin="other.example", reason="proxy_auth")

        report_proxy_failure(origin_probe, origin="a.example", reason="origin_block")

        now[0] += 60 * 60
        global_probe = p.select(origin="other.example", transport="httpx")
        report_proxy_success(global_probe, origin="other.example")
        assert p.select(origin="a.example", transport="httpx").half_open is True


class TestGetProvider:
    def test_webshare(self, monkeypatch):
        _set_provider(monkeypatch, "webshare", webshare="http://u:p@ws:7000")
        p = get_provider()
        assert p is not None
        assert p.name == "webshare"
        assert p.proxy_url() == "http://u:p@ws:7000"

    def test_webshare_pool_takes_precedence_over_legacy_url(self, monkeypatch):
        _set_provider(
            monkeypatch,
            "webshare",
            webshare="http://legacy:secret@old.example:7000",
            webshare_pool=(
                "http://pool-a:secret@p.webshare.io:10000",
                "http://pool-b:secret@p.webshare.io:10001",
            ),
        )
        p = get_provider()
        assert isinstance(p, PoolProxyProvider)
        assert p.select(origin=None, transport="httpx").url.startswith("http://pool-a:")

    def test_none(self, monkeypatch):
        _set_provider(monkeypatch, "none", webshare="http://u:p@ws:7000")
        assert get_provider() is None

    def test_unknown_provider_returns_none(self, monkeypatch):
        _set_provider(monkeypatch, "iproyal", webshare="http://u:p@ws:7000")
        assert get_provider() is None

    def test_webshare_without_url_returns_none(self, monkeypatch):
        _set_provider(monkeypatch, "webshare")
        assert get_provider() is None


class TestHttpxProxyFor:
    def test_opt_out_returns_none(self, monkeypatch):
        _set_provider(monkeypatch, "webshare", webshare="http://u:p@ws:7000")
        assert httpx_proxy_for(use_proxy=False) is None

    def test_opt_in_returns_url(self, monkeypatch):
        _set_provider(monkeypatch, "webshare", webshare="http://u:p@ws:7000")
        assert httpx_proxy_for(use_proxy=True) == "http://u:p@ws:7000"

    def test_opt_in_no_provider_returns_none(self, monkeypatch):
        _set_provider(monkeypatch, "none")
        assert httpx_proxy_for(use_proxy=True) is None

    def test_selected_provider_without_endpoint_fails_closed(self, monkeypatch):
        _set_provider(monkeypatch, "webshare")
        with pytest.raises(ProxyConfigurationError, match="no usable endpoint"):
            httpx_proxy_for(use_proxy=True)

    def test_pool_rotates_between_clients(self, monkeypatch):
        pool = (
            "http://pool-a:secret@p.webshare.io:10000",
            "http://pool-b:secret@p.webshare.io:10001",
        )
        _set_provider(monkeypatch, "webshare", webshare_pool=pool)

        assert [httpx_proxy_for(use_proxy=True) for _ in range(4)] == [*pool, *pool]


class TestPlaywrightProxyFor:
    def test_opt_out_returns_none(self, monkeypatch):
        _set_provider(monkeypatch, "webshare", webshare="http://u:p@ws:7000")
        assert playwright_proxy_for(use_proxy=False) is None

    def test_parses_credentials(self, monkeypatch):
        _set_provider(monkeypatch, "webshare", webshare="http://user:pass@host.example:7000")
        result = playwright_proxy_for(use_proxy=True)
        assert result == {
            "server": "http://host.example:7000",
            "username": "user",
            "password": "pass",
        }

    def test_no_credentials(self, monkeypatch):
        _set_provider(monkeypatch, "webshare", webshare="http://host.example:8080")
        result = playwright_proxy_for(use_proxy=True)
        assert result == {"server": "http://host.example:8080"}

    def test_none_when_disabled(self, monkeypatch):
        _set_provider(monkeypatch, "none")
        assert playwright_proxy_for(use_proxy=True) is None

    def test_pool_selects_one_affine_endpoint_per_browser_launch(self, monkeypatch):
        _set_provider(
            monkeypatch,
            "webshare",
            webshare_pool=(
                "http://pool%2Duser-a:secret%21@p.webshare.io:10000",
                "http://pool%2Duser-b:secret%21@p.webshare.io:10001",
            ),
        )

        first = playwright_proxy_for(use_proxy=True)
        second = playwright_proxy_for(use_proxy=True)

        assert first == {
            "server": "http://p.webshare.io:10000",
            "username": "pool-user-a",
            "password": "secret!",
        }
        assert second == {
            "server": "http://p.webshare.io:10001",
            "username": "pool-user-b",
            "password": "secret!",
        }
