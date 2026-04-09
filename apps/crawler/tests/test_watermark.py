"""Unit tests for src.core.monitors._watermark."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.core.monitors._watermark import WatermarkState, read, to_metadata_patch


class TestRead:
    def test_empty_metadata_returns_defaults(self):
        state = read(None, "pcsx_watermark")
        assert state.key == "pcsx_watermark"
        assert state.max_ts == 0
        assert state.last_full_at is None
        assert state.enabled is None
        assert state.auto_full_crawl is True
        assert state.interval_days == 7
        assert state.extra == {}

    def test_missing_key_returns_defaults(self):
        state = read({"sitemap_url": "https://x.com/s.xml"}, "pcsx_watermark")
        assert state.max_ts == 0
        assert state.enabled is None

    def test_non_dict_value_returns_defaults(self):
        state = read({"pcsx_watermark": "corrupt"}, "pcsx_watermark")
        assert state.max_ts == 0

    def test_parses_full_state(self):
        metadata = {
            "pcsx_watermark": {
                "max_ts": 1775606400,
                "last_full_at": "2026-04-01T12:00:00+00:00",
                "last_incremental_at": "2026-04-08T08:30:00+00:00",
                "interval_days": 14,
                "enabled": True,
                "auto_full_crawl": False,
                "extra": {"host": "careers.kering.com", "domain": "kering"},
            }
        }
        state = read(metadata, "pcsx_watermark")
        assert state.max_ts == 1775606400
        assert state.last_full_at == datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
        assert state.last_incremental_at == datetime(2026, 4, 8, 8, 30, 0, tzinfo=UTC)
        assert state.interval_days == 14
        assert state.enabled is True
        assert state.auto_full_crawl is False
        assert state.extra == {"host": "careers.kering.com", "domain": "kering"}

    def test_parses_z_suffix_datetime(self):
        metadata = {"pcsx_watermark": {"last_full_at": "2026-04-01T12:00:00Z"}}
        state = read(metadata, "pcsx_watermark")
        assert state.last_full_at == datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)

    def test_bad_datetime_becomes_none(self):
        metadata = {"pcsx_watermark": {"last_full_at": "not-a-date"}}
        state = read(metadata, "pcsx_watermark")
        assert state.last_full_at is None

    def test_non_int_max_ts_becomes_zero(self):
        metadata = {"pcsx_watermark": {"max_ts": None}}
        state = read(metadata, "pcsx_watermark")
        assert state.max_ts == 0

    def test_non_positive_interval_uses_default(self):
        metadata = {"pcsx_watermark": {"interval_days": 0}}
        state = read(metadata, "pcsx_watermark")
        assert state.interval_days == 7

    def test_non_bool_enabled_stays_none(self):
        metadata = {"pcsx_watermark": {"enabled": "yes"}}
        state = read(metadata, "pcsx_watermark")
        assert state.enabled is None


class TestNeedsFullCrawl:
    def test_first_run_triggers_full_crawl(self):
        state = WatermarkState(key="pcsx_watermark")
        assert state.needs_full_crawl() is True

    def test_zero_max_ts_triggers_full_crawl_even_with_last_full_at(self):
        state = WatermarkState(
            key="pcsx_watermark",
            max_ts=0,
            last_full_at=datetime.now(UTC),
        )
        assert state.needs_full_crawl() is True

    def test_recent_full_crawl_does_not_trigger(self):
        now = datetime(2026, 4, 8, 12, 0, 0, tzinfo=UTC)
        state = WatermarkState(
            key="pcsx_watermark",
            max_ts=12345,
            last_full_at=now - timedelta(days=3),
        )
        assert state.needs_full_crawl(now=now) is False

    def test_old_full_crawl_triggers(self):
        now = datetime(2026, 4, 8, 12, 0, 0, tzinfo=UTC)
        state = WatermarkState(
            key="pcsx_watermark",
            max_ts=12345,
            last_full_at=now - timedelta(days=10),
        )
        assert state.needs_full_crawl(now=now) is True

    def test_custom_interval(self):
        now = datetime(2026, 4, 8, 12, 0, 0, tzinfo=UTC)
        state = WatermarkState(
            key="pcsx_watermark",
            max_ts=12345,
            last_full_at=now - timedelta(days=5),
            interval_days=3,
        )
        assert state.needs_full_crawl(now=now) is True


class TestToMetadataPatch:
    def test_shape_is_shallow_merge_safe(self):
        """to_metadata_patch must return {key: full_dict} so the JSONB ``||``
        shallow merge in _UPDATE_METADATA replaces the subkey atomically
        rather than leaving stale inner keys behind."""
        state = WatermarkState(
            key="pcsx_watermark",
            max_ts=12345,
            last_full_at=datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC),
            enabled=True,
        )
        patch = to_metadata_patch(state)
        # Exactly one top-level key — the rest goes inside.
        assert list(patch.keys()) == ["pcsx_watermark"]
        inner = patch["pcsx_watermark"]
        assert inner["max_ts"] == 12345
        assert inner["enabled"] is True
        assert inner["last_full_at"] == "2026-04-01T00:00:00+00:00"

    def test_omits_unset_datetimes(self):
        state = WatermarkState(key="pcsx_watermark", max_ts=1)
        patch = to_metadata_patch(state)
        inner = patch["pcsx_watermark"]
        assert "last_full_at" not in inner
        assert "last_incremental_at" not in inner

    def test_omits_none_enabled(self):
        state = WatermarkState(key="pcsx_watermark", max_ts=1)
        assert "enabled" not in to_metadata_patch(state)["pcsx_watermark"]

    def test_omits_empty_extra(self):
        state = WatermarkState(key="pcsx_watermark", max_ts=1)
        assert "extra" not in to_metadata_patch(state)["pcsx_watermark"]

    def test_includes_extra_when_populated(self):
        state = WatermarkState(
            key="pcsx_watermark",
            max_ts=1,
            extra={"host": "x.com", "domain": "x"},
        )
        inner = to_metadata_patch(state)["pcsx_watermark"]
        assert inner["extra"] == {"host": "x.com", "domain": "x"}

    def test_roundtrip(self):
        """Serializing then reading should recover the original state."""
        original = WatermarkState(
            key="pcsx_watermark",
            max_ts=1775606400,
            last_full_at=datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC),
            last_incremental_at=datetime(2026, 4, 8, 8, 30, 0, tzinfo=UTC),
            interval_days=14,
            enabled=True,
            auto_full_crawl=False,
            extra={"host": "x.com"},
        )
        patch = to_metadata_patch(original)
        # Simulate what the DB would store: the inner dict becomes the value
        # at metadata[key].
        reconstructed_metadata = patch
        recovered = read(reconstructed_metadata, "pcsx_watermark")
        assert recovered.max_ts == original.max_ts
        assert recovered.last_full_at == original.last_full_at
        assert recovered.last_incremental_at == original.last_incremental_at
        assert recovered.interval_days == original.interval_days
        assert recovered.enabled == original.enabled
        assert recovered.auto_full_crawl == original.auto_full_crawl
        assert recovered.extra == original.extra
