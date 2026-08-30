"""Webshare proxy rotation, quarantine, and bounded recovery.

A board opts in with ``"proxy": true`` in its monitor or scraper config.
``PROXY_PROVIDER=none`` is the explicit direct-egress switch; ``webshare`` is
the only supported provider.

``WEBSHARE_PROXY_URLS`` contains credentialed, per-proxy backbone URLs on
``p.webshare.io``. Plain HTTP selects a pool member for every top-level httpx
request. Playwright selects once per browser launch so the document and all of
its subresources keep one exit IP—rotating browser subresources independently
is an anti-bot signal.

Hard transport/auth failures quarantine an endpoint globally. HTTP 403/429
responses quarantine it only for that target origin. Cooldowns are exponential
and bounded; an expired endpoint receives one half-open probe, so no endpoint
is discarded permanently. State is process-local and contains no raw target,
client, or exit IP metric labels.

``WEBSHARE_PROXY_URL`` remains a one-URL migration fallback. A direct address
in that variable becomes stale when Webshare's monthly replacement runs.
``WEBSHARE_API_KEY`` is operator-only and is not needed by this runtime module.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from threading import Lock
from typing import Literal, Protocol
from urllib.parse import unquote, urlparse

import structlog

from src.metrics import (
    proxy_client_selections_total,
    proxy_configuration_failures_total,
    proxy_endpoint_health_events_total,
)

log = structlog.get_logger()

ProxyTransport = Literal["httpx", "playwright"]
ProxyFailureReason = Literal[
    "proxy_auth",
    "proxy_transport",
    "origin_block",
    "origin_transport",
]
ProxyMode = Literal["backbone_pool", "legacy_direct"]


class ProxyConfigurationError(RuntimeError):
    """A proxy-required operation cannot use the selected provider."""


class ProxyPoolExhaustedError(ProxyConfigurationError):
    """Every endpoint is cooling down and none is ready for a recovery probe."""


class ProxyProvider(Protocol):
    name: str

    def select(self, *, origin: str | None, transport: ProxyTransport) -> ProxySelection:
        raise NotImplementedError

    def report_success(self, selection: ProxySelection, *, origin: str | None) -> None:
        raise NotImplementedError

    def report_failure(
        self,
        selection: ProxySelection,
        *,
        origin: str | None,
        reason: ProxyFailureReason,
    ) -> None:
        raise NotImplementedError

    def abandon(self, selection: ProxySelection, *, origin: str | None) -> None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ProxySelection:
    """One endpoint lease; the provider reference is deliberately not printable."""

    provider: str
    mode: ProxyMode
    url: str = field(repr=False)
    pool_slot: int
    pool_size: int
    half_open: bool
    _owner: ProxyProvider = field(repr=False, compare=False)
    _origin: str | None = field(repr=False, compare=False)
    _global_generation: int = field(repr=False, compare=False)
    _origin_generation: int | None = field(repr=False, compare=False)
    _global_probe: bool = field(repr=False, compare=False)
    _origin_probe: bool = field(repr=False, compare=False)


@dataclass(slots=True)
class _EndpointHealth:
    failures: int = 0
    quarantined_until: float = 0.0
    probe_due: bool = False
    probe_in_flight: bool = False
    # Every accepted failure/recovery advances the generation. Leases record
    # the generation they observed so a late response from an older request
    # cannot reopen or recover a newer circuit state.
    generation: int = 0


class PoolProxyProvider:
    """Round-robin Webshare endpoints with origin-aware circuit breaking."""

    _BASE_COOLDOWN_SECONDS = {
        "proxy_auth": 60 * 60,
        "proxy_transport": 2 * 60,
        "origin_block": 15 * 60,
        "origin_transport": 2 * 60,
    }
    _MAX_COOLDOWN_SECONDS = {
        "proxy_auth": 24 * 60 * 60,
        "proxy_transport": 60 * 60,
        "origin_block": 6 * 60 * 60,
        "origin_transport": 60 * 60,
    }
    _MAX_ORIGIN_HEALTH_ENTRIES = 10_000
    _GLOBAL_TRANSPORT_FAILURE_ORIGINS = 3
    _GLOBAL_TRANSPORT_FAILURE_WINDOW_SECONDS = 5 * 60

    def __init__(
        self,
        name: str,
        urls: tuple[str, ...],
        *,
        mode: ProxyMode = "backbone_pool",
        clock=time.monotonic,
        forced_slot: int | None = None,
    ) -> None:
        if not urls:
            raise ValueError("proxy pool must not be empty")
        self.name = name
        self._urls = urls
        self._mode: ProxyMode = mode
        self._clock = clock
        if forced_slot is not None and not 0 <= forced_slot < len(urls):
            raise ValueError("forced proxy slot is outside the pool")
        self._forced_slot = forced_slot
        self._cursor = 0
        self._global_health = [_EndpointHealth() for _ in urls]
        self._origin_health: OrderedDict[tuple[int, str], _EndpointHealth] = OrderedDict()
        self._transport_failure_origins: list[dict[str, float]] = [{} for _ in urls]
        self._lock = Lock()

    @staticmethod
    def _eligible(health: _EndpointHealth, now: float) -> bool:
        return health.quarantined_until <= now and not health.probe_in_flight

    def _origin_state(self, slot: int, origin: str | None) -> _EndpointHealth | None:
        if origin is None:
            return None
        key = (slot, origin)
        health = self._origin_health.get(key)
        if health is not None:
            self._origin_health.move_to_end(key)
        return health

    def _get_or_create_origin_state(self, slot: int, origin: str) -> _EndpointHealth:
        key = (slot, origin)
        health = self._origin_health.get(key)
        if health is not None:
            self._origin_health.move_to_end(key)
            return health
        if len(self._origin_health) >= self._MAX_ORIGIN_HEALTH_ENTRIES:
            self._origin_health.popitem(last=False)
            proxy_endpoint_health_events_total.labels(
                provider=self.name,
                scope="origin",
                event="evicted",
            ).inc()
        health = _EndpointHealth()
        self._origin_health[key] = health
        return health

    def _ordered_slots(self) -> list[int]:
        if self._forced_slot is not None:
            return [self._forced_slot]
        size = len(self._urls)
        return [(self._cursor + offset) % size for offset in range(size)]

    def select(self, *, origin: str | None, transport: ProxyTransport) -> ProxySelection:
        now = self._clock()
        with self._lock:
            ordered = self._ordered_slots()
            eligible: list[int] = []
            recovery: list[int] = []
            for slot in ordered:
                global_health = self._global_health[slot]
                origin_health = self._origin_state(slot, origin)
                if not self._eligible(global_health, now):
                    continue
                if origin_health is not None and not self._eligible(origin_health, now):
                    continue
                eligible.append(slot)
                if global_health.probe_due or (
                    origin_health is not None and origin_health.probe_due
                ):
                    recovery.append(slot)

            if not eligible:
                proxy_endpoint_health_events_total.labels(
                    provider=self.name,
                    scope="pool",
                    event="exhausted",
                ).inc()

                def quarantined_until(slot: int) -> float:
                    origin_health = self._origin_state(slot, origin)
                    return max(
                        self._global_health[slot].quarantined_until,
                        origin_health.quarantined_until if origin_health is not None else 0.0,
                    )

                next_retry = min(quarantined_until(slot) for slot in ordered)
                retry_after = max(1, int(next_retry - now))
                raise ProxyPoolExhaustedError(
                    f"all {self.name} proxy endpoints are cooling down; "
                    f"next recovery probe in about {retry_after}s"
                )

            if recovery:
                slot = recovery[0]
                global_health = self._global_health[slot]
                global_health.probe_due = False
                global_health.probe_in_flight = bool(global_health.failures)
                origin_health = self._origin_state(slot, origin)
                if origin_health is not None:
                    origin_health.probe_due = False
                    origin_health.probe_in_flight = bool(origin_health.failures)
                global_probe = global_health.probe_in_flight
                origin_probe = bool(origin_health and origin_health.probe_in_flight)
                half_open = global_probe or origin_probe
                proxy_endpoint_health_events_total.labels(
                    provider=self.name,
                    scope="origin" if origin_probe else "global",
                    event="half_open",
                ).inc()
            else:
                # A prior failure that has not reached quarantine would carry a
                # penalty here. Current hard-failure classes quarantine on the
                # first strike, but keeping the minimum-penalty selector makes
                # future soft degradation use the endpoint less without a new
                # scheduling primitive.
                def penalty(candidate: int) -> int:
                    origin_health = self._origin_state(candidate, origin)
                    return self._global_health[candidate].failures + (
                        origin_health.failures if origin_health is not None else 0
                    )

                minimum = min(penalty(slot) for slot in eligible)
                slot = next(slot for slot in eligible if penalty(slot) == minimum)
                half_open = False
                global_probe = False
                origin_probe = False

            self._cursor = (slot + 1) % len(self._urls)
            global_health = self._global_health[slot]
            origin_health = self._origin_state(slot, origin)
            global_generation = global_health.generation
            origin_generation = origin_health.generation if origin_health is not None else None

        selection = ProxySelection(
            provider=self.name,
            mode=self._mode,
            url=self._urls[slot],
            pool_slot=slot,
            pool_size=len(self._urls),
            half_open=half_open,
            _owner=self,
            _origin=origin,
            _global_generation=global_generation,
            _origin_generation=origin_generation,
            _global_probe=global_probe,
            _origin_probe=origin_probe,
        )
        proxy_client_selections_total.labels(
            provider=self.name,
            mode=self._mode,
            transport=transport,
        ).inc()
        log.debug(
            "proxy.endpoint.selected",
            provider=self.name,
            mode=self._mode,
            transport=transport,
            pool_slot=slot,
            pool_size=len(self._urls),
            half_open=half_open,
        )
        return selection

    @staticmethod
    def _recover_owned_probe(
        health: _EndpointHealth,
        *,
        selected_generation: int | None,
        owns_probe: bool,
    ) -> bool:
        if (
            not owns_probe
            or selected_generation is None
            or selected_generation != health.generation
            or not health.failures
            or not health.probe_in_flight
        ):
            return False
        health.failures = 0
        health.quarantined_until = 0.0
        health.probe_due = False
        health.probe_in_flight = False
        health.generation += 1
        return True

    @staticmethod
    def _release_owned_probe(
        health: _EndpointHealth,
        *,
        selected_generation: int | None,
        owns_probe: bool,
    ) -> bool:
        if (
            not owns_probe
            or selected_generation is None
            or selected_generation != health.generation
            or not health.probe_in_flight
        ):
            return False
        health.probe_in_flight = False
        health.probe_due = True
        return True

    def report_success(self, selection: ProxySelection, *, origin: str | None) -> None:
        recovered_scopes: list[str] = []
        with self._lock:
            global_health = self._global_health[selection.pool_slot]
            # A current-generation success is useful global transport
            # evidence. A stale success is ignored completely.
            if selection._global_generation == global_health.generation:
                self._transport_failure_origins[selection.pool_slot].clear()
            if self._recover_owned_probe(
                global_health,
                selected_generation=selection._global_generation,
                owns_probe=selection._global_probe,
            ):
                recovered_scopes.append("global")
            if selection._origin is not None:
                origin_health = self._origin_state(selection.pool_slot, selection._origin)
                if origin_health is not None and self._recover_owned_probe(
                    origin_health,
                    selected_generation=selection._origin_generation,
                    owns_probe=selection._origin_probe,
                ):
                    recovered_scopes.append("origin")
        for scope in recovered_scopes:
            proxy_endpoint_health_events_total.labels(
                provider=self.name,
                scope=scope,
                event="recovered",
            ).inc()
            log.info(
                "proxy.endpoint.recovered",
                provider=self.name,
                scope=scope,
                pool_slot=selection.pool_slot,
            )

    def report_failure(
        self,
        selection: ProxySelection,
        *,
        origin: str | None,
        reason: ProxyFailureReason,
    ) -> None:
        scope = (
            "origin"
            if reason in {"origin_block", "origin_transport"} and origin is not None
            else "global"
        )
        now = self._clock()
        with self._lock:
            global_health = self._global_health[selection.pool_slot]
            selected_origin_health = (
                self._origin_state(selection.pool_slot, selection._origin)
                if selection._origin is not None
                else None
            )
            if selection._global_generation != global_health.generation:
                # Global and origin circuits advance independently. A
                # concurrent global transition makes this result stale for
                # classification, but it must not strand an origin probe that
                # the selection still owns.
                if selected_origin_health is not None:
                    self._release_owned_probe(
                        selected_origin_health,
                        selected_generation=selection._origin_generation,
                        owns_probe=selection._origin_probe,
                    )
                return

            existing_origin = (
                self._origin_state(selection.pool_slot, origin) if origin is not None else None
            )
            if scope == "origin":
                assert origin is not None
                if origin != selection._origin:
                    if selected_origin_health is not None:
                        # Reaching a cross-origin final request proves the
                        # selected origin returned a redirect. Resolve that
                        # origin's probe before checking whether the redirect
                        # lease is allowed to mutate the final origin.
                        self._recover_owned_probe(
                            selected_origin_health,
                            selected_generation=selection._origin_generation,
                            owns_probe=selection._origin_probe,
                        )
                    if existing_origin is not None:
                        # An old redirect-chain lease must not mutate existing
                        # final-origin state. It still owns the current global
                        # probe, and reaching the redirect proves that probe.
                        self._recover_owned_probe(
                            global_health,
                            selected_generation=selection._global_generation,
                            owns_probe=selection._global_probe,
                        )
                        return
                elif existing_origin is None:
                    if reason == "origin_block":
                        self._recover_owned_probe(
                            global_health,
                            selected_generation=selection._global_generation,
                            owns_probe=selection._global_probe,
                        )
                    else:
                        self._release_owned_probe(
                            global_health,
                            selected_generation=selection._global_generation,
                            owns_probe=selection._global_probe,
                        )
                    if selection._origin_generation is not None:
                        return
                elif selection._origin_generation != existing_origin.generation:
                    if reason == "origin_block":
                        self._recover_owned_probe(
                            global_health,
                            selected_generation=selection._global_generation,
                            owns_probe=selection._global_probe,
                        )
                    else:
                        self._release_owned_probe(
                            global_health,
                            selected_generation=selection._global_generation,
                            owns_probe=selection._global_probe,
                        )
                    return

            cooldown_reason = reason
            if reason == "origin_transport" and origin is not None:
                evidence = self._transport_failure_origins[selection.pool_slot]
                oldest = now - self._GLOBAL_TRANSPORT_FAILURE_WINDOW_SECONDS
                for known_origin, failed_at in list(evidence.items()):
                    if failed_at < oldest:
                        evidence.pop(known_origin, None)
                evidence[origin] = now
                if len(evidence) >= self._GLOBAL_TRANSPORT_FAILURE_ORIGINS:
                    # Three distinct origins failing through one slot in a
                    # short window is much stronger evidence of a bad proxy
                    # than of unrelated target outages.
                    scope = "global"
                    cooldown_reason = "proxy_transport"
                    evidence.clear()
            if scope == "origin":
                assert origin is not None
                if reason == "origin_block" or origin != selection._origin:
                    # A concrete origin response proves an in-flight global
                    # recovery probe reached Webshare. A cross-origin request
                    # also proves the selected origin returned a redirect,
                    # even if the final origin later failed to connect.
                    self._recover_owned_probe(
                        global_health,
                        selected_generation=selection._global_generation,
                        owns_probe=selection._global_probe,
                    )
                else:
                    # A target connect/TLS failure is inconclusive for a
                    # global half-open probe; keep that probe due.
                    self._release_owned_probe(
                        global_health,
                        selected_generation=selection._global_generation,
                        owns_probe=selection._global_probe,
                    )
                if origin != selection._origin:
                    # Cross-origin redirects were not selected against this
                    # origin's circuit. Permit only the first failure; never
                    # let an old redirect-chain lease mutate existing state.
                    health = self._get_or_create_origin_state(selection.pool_slot, origin)
                else:
                    if existing_origin is None:
                        health = self._get_or_create_origin_state(selection.pool_slot, origin)
                    else:
                        health = existing_origin
            else:
                # A failed global probe cannot say whether an expired
                # origin-specific quarantine recovered. Put that origin probe
                # back into the due state for the next globally healthy use.
                if selected_origin_health is not None and selected_origin_health.failures:
                    self._release_owned_probe(
                        selected_origin_health,
                        selected_generation=selection._origin_generation,
                        owns_probe=selection._origin_probe,
                    )
                health = global_health
            health.failures += 1
            base = self._BASE_COOLDOWN_SECONDS[cooldown_reason]
            maximum = self._MAX_COOLDOWN_SECONDS[cooldown_reason]
            cooldown = min(maximum, base * (2 ** (health.failures - 1)))
            health.quarantined_until = now + cooldown
            health.probe_due = True
            health.probe_in_flight = False
            health.generation += 1

        proxy_endpoint_health_events_total.labels(
            provider=self.name,
            scope=scope,
            event="quarantined",
        ).inc()
        log.warning(
            "proxy.endpoint.quarantined",
            provider=self.name,
            scope=scope,
            reason=("proxy_transport_multi_origin" if cooldown_reason != reason else reason),
            pool_slot=selection.pool_slot,
            pool_size=selection.pool_size,
            cooldown_seconds=cooldown,
        )

    def abandon(self, selection: ProxySelection, *, origin: str | None) -> None:
        """Release an inconclusive half-open lease without declaring recovery."""

        with self._lock:
            global_health = self._global_health[selection.pool_slot]
            self._release_owned_probe(
                global_health,
                selected_generation=selection._global_generation,
                owns_probe=selection._global_probe,
            )
            origin_health = (
                self._origin_state(selection.pool_slot, selection._origin)
                if selection._origin is not None
                else None
            )
            if origin_health is not None:
                self._release_owned_probe(
                    origin_health,
                    selected_generation=selection._origin_generation,
                    owns_probe=selection._origin_probe,
                )


class StaticProxyProvider(PoolProxyProvider):
    """One-URL migration fallback with the same quarantine/recovery behavior."""

    def __init__(self, name: str, url: str, *, forced_slot: int | None = None) -> None:
        self._url = url
        if url:
            super().__init__(name, (url,), mode="legacy_direct", forced_slot=forced_slot)
        else:
            # Preserve the historical diagnostic accessor for empty URLs. An
            # empty provider is never returned by the runtime factory.
            self.name = name

    def proxy_url(self) -> str | None:
        return self._url or None


@lru_cache(maxsize=8)
def _provider_for_values(
    provider_name: str,
    webshare_urls: tuple[str, ...],
    webshare_url: str,
    webshare_canary_slot: int | None,
) -> ProxyProvider | None:
    """Build one health-owning provider per immutable environment snapshot."""

    if provider_name == "none":
        return None
    if provider_name == "webshare":
        if webshare_urls:
            return PoolProxyProvider(
                "webshare",
                webshare_urls,
                forced_slot=webshare_canary_slot,
            )
        if webshare_url:
            log.warning("proxy.provider.legacy_direct", provider="webshare")
            return StaticProxyProvider(
                "webshare",
                webshare_url,
                forced_slot=webshare_canary_slot,
            )
        log.error("proxy.provider.missing_url", provider="webshare")
        return None
    log.error("proxy.provider.unknown", provider=provider_name)
    return None


def _settings_snapshot() -> tuple[str, tuple[str, ...], str, int | None] | None:
    try:
        from src.config import settings
    except Exception:
        return None
    return (
        settings.proxy_provider,
        tuple(settings.webshare_proxy_urls),
        settings.webshare_proxy_url,
        settings.webshare_proxy_canary_slot,
    )


def get_provider() -> ProxyProvider | None:
    """Return the configured provider, or ``None`` for explicit direct egress."""

    snapshot = _settings_snapshot()
    return _provider_for_values(*snapshot) if snapshot is not None else None


def require_provider(*, use_proxy: bool) -> ProxyProvider | None:
    """Resolve the provider, failing closed for a selected unusable provider."""

    if not use_proxy:
        return None
    snapshot = _settings_snapshot()
    provider_name = snapshot[0] if snapshot is not None else "unavailable"
    if provider_name == "none":
        return None
    provider = _provider_for_values(*snapshot) if snapshot is not None else None
    if provider is not None:
        return provider

    reason = "settings_unavailable" if snapshot is None else "missing_endpoint"
    proxy_configuration_failures_total.labels(
        provider=provider_name,
        reason=reason,
    ).inc()
    log.error("proxy.selection.rejected", provider=provider_name, reason=reason)
    raise ProxyConfigurationError(
        f"proxy provider {provider_name!r} is selected but has no usable endpoint"
    )


def select_proxy(
    *,
    use_proxy: bool,
    transport: ProxyTransport,
    origin: str | None = None,
) -> ProxySelection | None:
    provider = require_provider(use_proxy=use_proxy)
    if provider is None:
        return None
    return provider.select(origin=origin, transport=transport)


def report_proxy_success(selection: ProxySelection, *, origin: str | None = None) -> None:
    selection._owner.report_success(selection, origin=origin)


def report_proxy_failure(
    selection: ProxySelection,
    *,
    reason: ProxyFailureReason,
    origin: str | None = None,
) -> None:
    selection._owner.report_failure(selection, origin=origin, reason=reason)


def abandon_proxy_selection(
    selection: ProxySelection,
    *,
    origin: str | None = None,
) -> None:
    selection._owner.abandon(selection, origin=origin)


def httpx_proxy_for(*, use_proxy: bool) -> str | None:
    """Compatibility helper selecting one endpoint for a caller-owned client."""

    selection = select_proxy(use_proxy=use_proxy, transport="httpx")
    return selection.url if selection is not None else None


def playwright_proxy_selection_for(
    *,
    use_proxy: bool,
    origin: str | None = None,
) -> tuple[dict[str, str] | None, ProxySelection | None]:
    """Select one affine endpoint and Playwright launch dictionary."""

    selection = select_proxy(
        use_proxy=use_proxy,
        transport="playwright",
        origin=origin,
    )
    if selection is None:
        return None, None

    parsed = urlparse(selection.url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProxyConfigurationError("selected Webshare endpoint has an invalid port") from exc
    if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname or port is None:
        raise ProxyConfigurationError("selected Webshare endpoint is invalid")

    out = {"server": f"{parsed.scheme}://{parsed.hostname}:{port}"}
    if parsed.username:
        out["username"] = unquote(parsed.username)
    if parsed.password is not None:
        out["password"] = unquote(parsed.password)
    return out, selection


def playwright_proxy_for(*, use_proxy: bool) -> dict[str, str] | None:
    """Compatibility wrapper for callers that do not report health feedback."""

    proxy, _selection = playwright_proxy_selection_for(use_proxy=use_proxy)
    return proxy
