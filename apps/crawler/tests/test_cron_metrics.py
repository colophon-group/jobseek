"""Tests for the cron execution-metrics helper (#2704)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.cron_metrics import cron_run


async def _raise_inside_cron_run(job: str) -> None:
    async with cron_run(job):
        raise RuntimeError("boom")


class TestCronRunStructuredLogs:
    """``cron_run`` always emits structured logs (start + complete)
    regardless of Pushgateway configuration. Tests assert the log
    events, not the Pushgateway path (which is opt-in).
    """

    @pytest.mark.asyncio
    async def test_success_path_emits_start_and_complete(self):
        events: list[tuple[str, dict]] = []

        def _capture(event: str, **kw):
            events.append((event, kw))

        with patch("src.cron_metrics.log") as mock_log:
            mock_log.info.side_effect = _capture

            async with cron_run("test-job"):
                pass

        # cron.start, cron.complete (status=success) — both via log.info.
        # Pushgateway path is no-op without env var.
        info_events = [e for e in events]
        assert info_events[0][0] == "cron.start"
        assert info_events[0][1]["job"] == "test-job"

        complete = info_events[-1]
        assert complete[0] == "cron.complete"
        assert complete[1]["job"] == "test-job"
        assert complete[1]["status"] == "success"
        assert "duration_s" in complete[1]

    @pytest.mark.asyncio
    async def test_failure_path_records_status_and_reraises(self):
        events: list[tuple[str, dict]] = []

        def _capture_info(event: str, **kw):
            events.append(("info", event, kw))

        def _capture_exception(event: str, **kw):
            events.append(("exception", event, kw))

        with patch("src.cron_metrics.log") as mock_log:
            mock_log.info.side_effect = _capture_info
            mock_log.exception.side_effect = _capture_exception

            with pytest.raises(RuntimeError, match="boom"):
                await _raise_inside_cron_run("test-job")

        # cron.start (info) + cron.complete with status=failure (exception).
        assert events[0] == ("info", "cron.start", {"job": "test-job"})
        complete = events[-1]
        assert complete[0] == "exception"
        assert complete[1] == "cron.complete"
        assert complete[2]["status"] == "failure"
        assert complete[2]["job"] == "test-job"

    @pytest.mark.asyncio
    async def test_duration_is_recorded(self):
        """The ``duration_s`` field is non-negative and includes time
        spent inside the body."""
        import asyncio

        captured: list[dict] = []

        def _capture(event: str, **kw):
            if event == "cron.complete":
                captured.append(kw)

        with patch("src.cron_metrics.log") as mock_log:
            mock_log.info.side_effect = _capture
            mock_log.exception.side_effect = _capture

            async with cron_run("test-job"):
                await asyncio.sleep(0.01)

        assert len(captured) == 1
        assert captured[0]["duration_s"] >= 0.01


class TestCronRunPushgateway:
    """Pushgateway emission is opt-in via ``CRAWLER_PUSHGATEWAY_URL``.
    Without the env var, the push is a no-op. With it, the helper
    constructs a registry, sets the metrics, and pushes — failures
    in the push are swallowed so observability outages don't propagate
    into the cron run itself.
    """

    @pytest.mark.asyncio
    async def test_no_env_var_skips_push(self, monkeypatch):
        monkeypatch.delenv("CRAWLER_PUSHGATEWAY_URL", raising=False)
        push_mock = patch("prometheus_client.push_to_gateway")
        with push_mock as mock_push:
            async with cron_run("test-job"):
                pass
        # No push happened.
        assert mock_push.call_count == 0

    @pytest.mark.asyncio
    async def test_env_var_triggers_push_on_success(self, monkeypatch):
        monkeypatch.setenv("CRAWLER_PUSHGATEWAY_URL", "http://pushgateway:9091")
        with patch("prometheus_client.push_to_gateway") as mock_push:
            async with cron_run("test-job"):
                pass

        assert mock_push.call_count == 1
        call = mock_push.call_args
        # First positional arg is the URL.
        assert call.args[0] == "http://pushgateway:9091"
        # Pushgateway grouping key includes the cron job name so concurrent
        # runs of different cron jobs don't overwrite each other's series.
        assert call.kwargs["grouping_key"] == {"cron_job": "test-job"}

    @pytest.mark.asyncio
    async def test_env_var_triggers_push_on_failure(self, monkeypatch):
        monkeypatch.setenv("CRAWLER_PUSHGATEWAY_URL", "http://pushgateway:9091")
        with (
            patch("prometheus_client.push_to_gateway") as mock_push,
            pytest.raises(RuntimeError),
        ):
            await _raise_inside_cron_run("test-job")

        # Push fired even on failure — that's how operators see "this
        # job is running, but it's failing".
        assert mock_push.call_count == 1

    @pytest.mark.asyncio
    async def test_pushgateway_failure_is_swallowed(self, monkeypatch):
        """A Pushgateway outage must NOT propagate into the cron run.
        The push is observability — failure here shouldn't fail a
        successful refresh-typesense."""
        monkeypatch.setenv("CRAWLER_PUSHGATEWAY_URL", "http://pushgateway:9091")
        with patch(
            "prometheus_client.push_to_gateway",
            side_effect=ConnectionError("pushgateway unreachable"),
        ):
            # Body succeeds; only the push fails. The exception must be
            # swallowed so the cron run completes normally.
            async with cron_run("test-job"):
                pass

    @pytest.mark.asyncio
    async def test_pushgateway_failure_does_not_mask_body_exception(self, monkeypatch):
        """If the body raises AND the push raises, the body's
        exception still propagates (the original failure isn't
        swallowed by an observability hiccup)."""
        monkeypatch.setenv("CRAWLER_PUSHGATEWAY_URL", "http://pushgateway:9091")
        with (
            patch(
                "prometheus_client.push_to_gateway",
                side_effect=ConnectionError("pushgateway unreachable"),
            ),
            pytest.raises(RuntimeError, match="business error"),
        ):
            async with cron_run("test-job"):
                raise RuntimeError("business error")

    @pytest.mark.asyncio
    async def test_metric_labels_correct_success(self, monkeypatch):
        """The pushed registry contains both gauges with the expected
        label values and value semantics for a successful run.
        """
        monkeypatch.setenv("CRAWLER_PUSHGATEWAY_URL", "http://pushgateway:9091")

        captured_registry = []

        def _capture_registry(*args, **kwargs):
            captured_registry.append(kwargs.get("registry"))

        with patch("prometheus_client.push_to_gateway", side_effect=_capture_registry):
            async with cron_run("refresh-typesense"):
                pass

        assert len(captured_registry) == 1
        registry = captured_registry[0]

        # Both gauges are present (prometheus_client uses the metric
        # name as-is for gauges).
        names = {m.name for m in registry.collect()}
        assert "crawler_cron_last_run_ts" in names
        assert "crawler_cron_last_run_status" in names

        samples_by_name = {}
        for metric in registry.collect():
            for sample in metric.samples:
                samples_by_name[sample.name] = sample

        ts_sample = samples_by_name["crawler_cron_last_run_ts"]
        assert ts_sample.labels == {"job": "refresh-typesense"}
        assert ts_sample.value > 0

        status_sample = samples_by_name["crawler_cron_last_run_status"]
        assert status_sample.labels == {"job": "refresh-typesense"}
        assert status_sample.value == 1.0  # success

    @pytest.mark.asyncio
    async def test_metric_labels_correct_failure(self, monkeypatch):
        """A failed run pushes ``last_run_status = 0``."""
        monkeypatch.setenv("CRAWLER_PUSHGATEWAY_URL", "http://pushgateway:9091")

        captured_registry = []

        def _capture_registry(*args, **kwargs):
            captured_registry.append(kwargs.get("registry"))

        with (
            patch("prometheus_client.push_to_gateway", side_effect=_capture_registry),
            pytest.raises(RuntimeError),
        ):
            await _raise_inside_cron_run("refresh-typesense")

        assert len(captured_registry) == 1
        registry = captured_registry[0]
        for metric in registry.collect():
            for sample in metric.samples:
                if sample.name == "crawler_cron_last_run_status":
                    assert sample.value == 0.0  # failure

    @pytest.mark.asyncio
    async def test_pushgateway_grouping_key_keeps_jobs_separate(self, monkeypatch):
        """Different cron jobs MUST push under different
        ``grouping_key`` values so they don't clobber each other's
        series under Pushgateway's PUT semantics.
        """
        monkeypatch.setenv("CRAWLER_PUSHGATEWAY_URL", "http://pushgateway:9091")

        grouping_keys = []

        def _capture(*args, **kwargs):
            grouping_keys.append(kwargs.get("grouping_key"))

        with patch("prometheus_client.push_to_gateway", side_effect=_capture):
            async with cron_run("refresh-typesense"):
                pass
            async with cron_run("backfill-typesense"):
                pass

        assert grouping_keys == [
            {"cron_job": "refresh-typesense"},
            {"cron_job": "backfill-typesense"},
        ]
