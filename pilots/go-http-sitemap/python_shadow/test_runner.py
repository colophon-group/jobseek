from __future__ import annotations

import asyncio
import contextlib
import csv
import hashlib
import http.cookiejar
import io
import json
import struct
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "crawler"))

from runner import (
    HASH_ALGORITHM,
    MAX_CONNECTIONS,
    AttemptLimitError,
    ConnectionProbe,
    ExactTargetTransport,
    ManifestError,
    ResponseBodyTooLargeError,
    RoundMeter,
    SitemapIndexRejectedError,
    TargetRejectedError,
    _cgroup_event,
    _cgroup_value,
    _current_rss_bytes,
    _fetch_and_extract,
    _final_invariants_valid,
    _monitor_config,
    _RejectAllCookiePolicy,
    _run_round,
    canonical_url_sha256,
    decode_manifest,
    failure_report,
    main,
    parse_profile,
    run,
)


def manifest_document() -> dict:
    return {
        "schema_version": 1,
        "jobs": [
            {
                "id": f"source-{index:02d}",
                "board_url": f"https://source-{index:02d}.example/jobs",
                "sitemap_url": f"https://source-{index:02d}.example/sitemap.xml",
                "include_literal": "/jobs/",
                "exclude_literal": "",
                "max_urls": 50000,
                "max_index_children": 1,
            }
            for index in range(16)
        ],
    }


def manifest_bytes() -> bytes:
    return json.dumps(manifest_document()).encode()


class ManifestTests(unittest.TestCase):
    def test_committed_fleet_exactly_mirrors_production_sitemap_configs(self) -> None:
        repository = Path(__file__).resolve().parents[3]
        fleet = decode_manifest(
            (repository / "pilots/go-http-sitemap/benchmark/fleet.json").read_bytes()
        )
        with (repository / "apps/crawler/data/boards.csv").open(
            newline="", encoding="utf-8"
        ) as source:
            boards = {row["board_slug"]: row for row in csv.DictReader(source)}

        self.assertEqual(len(fleet.jobs), 32)
        self.assertEqual(len({job.id for job in fleet.jobs}), 32)
        self.assertEqual(len({urlparse(job.sitemap_url).netloc for job in fleet.jobs}), 32)
        for job in fleet.jobs:
            with self.subTest(job=job.id):
                board = boards[job.id]
                config = json.loads(board["monitor_config"])
                self.assertEqual(board["monitor_type"], "sitemap")
                self.assertEqual(board["board_url"], job.board_url)
                self.assertLessEqual(set(config), {"sitemap_url", "url_filter"})
                self.assertEqual(config["sitemap_url"], job.sitemap_url)
                url_filter = config.get("url_filter", "")
                self.assertIsInstance(url_filter, str)
                self.assertEqual(url_filter, job.include_literal)
                self.assertEqual(job.exclude_literal, "")
                self.assertEqual(job.max_urls, 50_000)
                self.assertEqual(job.max_index_children, 1)

    def test_accepts_exact_fixed_literal_only_distinct_origin_manifest(self) -> None:
        manifest = decode_manifest(manifest_bytes())
        self.assertEqual(len(manifest.jobs), 16)

    def test_rejects_wrong_fleet_size_unknown_fields_and_index_budget(self) -> None:
        variants = []
        short = manifest_document()
        short["jobs"] = short["jobs"][:-1]
        variants.append(short)
        unknown = manifest_document()
        unknown["extra"] = True
        variants.append(unknown)
        index = manifest_document()
        index["jobs"][0]["max_index_children"] = 0
        variants.append(index)
        for document in variants:
            with self.subTest(document=document), self.assertRaises(ManifestError):
                decode_manifest(json.dumps(document).encode())

    def test_rejects_regex_metacharacters_and_duplicate_origins(self) -> None:
        regex = manifest_document()
        regex["jobs"][0]["include_literal"] = r"^/jobs/.+$"
        duplicate = manifest_document()
        duplicate["jobs"][1]["sitemap_url"] = duplicate["jobs"][0]["sitemap_url"]
        for document in (regex, duplicate):
            with self.subTest(document=document), self.assertRaises(ManifestError):
                decode_manifest(json.dumps(document).encode())

    def test_allows_exact_production_empty_filter(self) -> None:
        document = manifest_document()
        document["jobs"][0]["include_literal"] = ""
        self.assertEqual(decode_manifest(json.dumps(document).encode()).jobs[0].include_literal, "")

    def test_rejects_private_literal_and_non_https_urls(self) -> None:
        for invalid in ("http://public.example/sitemap.xml", "https://127.0.0.1/a.xml"):
            document = manifest_document()
            document["jobs"][0]["sitemap_url"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ManifestError):
                decode_manifest(json.dumps(document).encode())


class ContractTests(unittest.TestCase):
    def test_profiles_include_primary_c5_and_exactly_two_rounds_are_runtime_fixed(
        self,
    ) -> None:
        for profile, concurrency in (
            ("c2", 2),
            ("c4", 4),
            ("c5", 5),
            ("c8", 8),
            ("c12", 12),
            ("c16", 16),
        ):
            self.assertEqual(parse_profile(["--profile", profile]), (profile, concurrency))
        for argv in (["--profile", "c1"], ["--profile", "c4", "extra"], []):
            with self.subTest(argv=argv), self.assertRaises(ManifestError):
                parse_profile(argv)

    def test_hash_is_go_compatible_and_unambiguous(self) -> None:
        urls = {"https://example.test/a", "https://example.test/b"}
        expected = hashlib.sha256()
        for url in sorted(urls):
            encoded = url.encode()
            expected.update(struct.pack(">Q", len(encoded)))
            expected.update(encoded)
        self.assertEqual(canonical_url_sha256(urls), expected.hexdigest())
        self.assertEqual(
            canonical_url_sha256(urls),
            "7cffdea8633f0f039710fea2eb66861eaa858210ea28221f0865bafd469e4bd4",
        )
        self.assertEqual(HASH_ALGORITHM, "sha256-length-prefixed-v1")

    def test_monitor_config_preserves_literal_regex_semantics(self) -> None:
        job = decode_manifest(manifest_bytes()).jobs[0]
        self.assertEqual(
            _monitor_config(job),
            {"sitemap_url": job.sitemap_url, "url_filter": "/jobs/"},
        )

    def test_failure_wire_report_is_sanitized(self) -> None:
        encoded = json.dumps(failure_report("internal", 0.0))
        self.assertNotIn("secret.example", encoded)
        self.assertNotIn("Traceback", encoded)
        self.assertNotIn("Exception", encoded)

    def test_rejected_arguments_emit_one_json_line_and_no_stderr(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = main(["--profile", "https://secret.example/?token=do-not-emit"])
        lines = stdout.getvalue().splitlines()
        self.assertEqual(status, 1)
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["error_kind"], "arguments_rejected")
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn("secret.example", lines[0])
        self.assertNotIn("do-not-emit", lines[0])


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler=None) -> None:
        self.calls: list[httpx.Request] = []
        self.handler = handler or (
            lambda request: httpx.Response(200, content=b"ok", request=request)
        )

    async def handle_async_request(self, request):
        self.calls.append(request)
        return self.handler(request)


class ExactTargetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.job = decode_manifest(manifest_bytes()).jobs[0]
        self.meter = RoundMeter()
        self.meter.reset((self.job,))
        self.inner = RecordingTransport()
        self.transport = ExactTargetTransport.make(httpx, self.inner, self.meter)
        self.token = __import__("runner")._current_job.set(self.job.id)

    async def asyncTearDown(self) -> None:
        __import__("runner")._current_job.reset(self.token)
        await self.transport.aclose()

    def request(self, url: str | None = None, **headers: str) -> httpx.Request:
        defaults = {
            "User-Agent": "jobseek-crawler (+https://jseek.co/)",
            "Accept": "application/xml,text/xml,*/*;q=0.8",
            "Accept-Encoding": "identity",
        }
        defaults.update(headers)
        return httpx.Request("GET", url or self.job.sitemap_url, headers=defaults)

    async def test_exact_first_target_reaches_wire_once(self) -> None:
        await self.transport.handle_async_request(self.request())
        self.assertEqual(len(self.inner.calls), 1)
        attempt = self.meter.attempts[self.job.id]
        self.assertEqual((attempt.requests, attempt.wire_attempts), (1, 1))

    async def test_redirect_retry_and_sensitive_headers_are_rejected_before_wire(
        self,
    ) -> None:
        with self.assertRaises(TargetRejectedError):
            await self.transport.handle_async_request(self.request(self.job.board_url))
        self.assertEqual(self.inner.calls, [])

        await self.transport.handle_async_request(self.request())
        with self.assertRaises(AttemptLimitError):
            await self.transport.handle_async_request(self.request())
        self.assertEqual(len(self.inner.calls), 1)

        self.meter.reset((self.job,))
        for header in ("Authorization", "Proxy-Authorization", "Cookie"):
            with self.subTest(header=header), self.assertRaises(TargetRejectedError):
                await self.transport.handle_async_request(self.request(**{header: "secret"}))
        self.assertEqual(len(self.inner.calls), 1)

    async def test_transport_failure_still_blocks_a_second_logical_request(
        self,
    ) -> None:
        class FailingTransport(httpx.AsyncBaseTransport):
            calls = 0

            async def handle_async_request(self, request):
                del request
                self.calls += 1
                raise httpx.ConnectError("secret detail")

        failing = FailingTransport()
        transport = ExactTargetTransport.make(httpx, failing, self.meter)
        with self.assertRaises(httpx.ConnectError):
            await transport.handle_async_request(self.request())
        self.assertEqual(self.meter.attempts[self.job.id].wire_attempts, 0)
        with self.assertRaises(AttemptLimitError):
            await transport.handle_async_request(self.request())
        self.assertEqual(failing.calls, 1)

    async def test_reject_all_cookie_jar_keeps_warm_request_cookie_free(self) -> None:
        inner = RecordingTransport(
            lambda request: httpx.Response(
                200,
                headers={"set-cookie": "session=secret; Path=/"},
                content=b"ok",
                request=request,
            )
        )
        transport = ExactTargetTransport.make(httpx, inner, self.meter)
        jar = http.cookiejar.CookieJar(policy=_RejectAllCookiePolicy())
        async with httpx.AsyncClient(transport=transport, cookies=jar) as client:
            await client.get(self.job.sitemap_url, headers=self.request().headers)
            self.meter.reset((self.job,))
            await client.get(self.job.sitemap_url, headers=self.request().headers)
        self.assertEqual(len(inner.calls), 2)
        self.assertNotIn("cookie", {key.lower() for key in inner.calls[1].headers})


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.read_chunks = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read_chunks += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class FakeMonitor:
    @dataclass(slots=True)
    class MonitorResult:
        urls: set[str]
        filtered_count: int = 0

    @staticmethod
    def _apply_url_filter(result, config):
        literal = config["url_filter"]
        urls = {url for url in result.urls if literal in url}
        return FakeMonitor.MonitorResult(urls, len(result.urls) - len(urls))


class FakeSitemap:
    @staticmethod
    def _extract_urls(root):
        return [element.text for element in root.iter() if element.tag.endswith("loc")]


class TDMReservedError(Exception):
    pass


class FakeTDM:
    @staticmethod
    def check_response(response, *, body_excerpt=None):
        del body_excerpt
        if response.headers.get("tdm-reservation") == "1":
            raise TDMReservedError


class FetchSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, response: httpx.Response):
        job = decode_manifest(manifest_bytes()).jobs[0]

        async def handler(request):
            response.request = request
            return response

        class Client:
            def stream(self, *args, **kwargs):
                request = httpx.Request(args[0], args[1], headers=kwargs["headers"])

                class Context:
                    async def __aenter__(self):
                        return await handler(request)

                    async def __aexit__(self, *exc):
                        return False

                return Context()

        return await _fetch_and_extract(
            job,
            client=Client(),
            production_monitor=FakeMonitor,
            production_sitemap=FakeSitemap,
            production_tdm=FakeTDM,
            connection_probe=SimpleNamespace(sample=lambda: 1),
        )

    async def test_tdm_header_rejects_before_body_read(self) -> None:
        stream = TrackingStream([b"<urlset/>"])
        response = httpx.Response(200, headers={"tdm-reservation": "1"}, stream=stream)
        with self.assertRaises(TDMReservedError):
            await self.fetch(response)
        self.assertEqual(stream.read_chunks, 0)

    async def test_streaming_identity_body_cap(self) -> None:
        stream = TrackingStream([b"a" * (8 << 20), b"x"])
        response = httpx.Response(200, headers={"content-encoding": "identity"}, stream=stream)
        with self.assertRaises(ResponseBodyTooLargeError):
            await self.fetch(response)
        self.assertEqual(stream.read_chunks, 2)

    async def test_real_streaming_context_closes_on_tdm_and_body_cap(self) -> None:
        job = decode_manifest(manifest_bytes()).jobs[0]
        cases = (
            (
                TrackingStream([b"<urlset/>"]),
                {"tdm-reservation": "1"},
                TDMReservedError,
            ),
            (
                TrackingStream([b"a" * (8 << 20), b"x"]),
                {"content-encoding": "identity"},
                ResponseBodyTooLargeError,
            ),
        )
        for stream, headers, expected in cases:
            with self.subTest(expected=expected):
                transport = httpx.MockTransport(
                    lambda request, stream=stream, headers=headers: httpx.Response(
                        200,
                        headers=headers,
                        stream=stream,
                        request=request,
                    )
                )
                async with httpx.AsyncClient(transport=transport) as client:
                    with self.assertRaises(expected):
                        await _fetch_and_extract(
                            job,
                            client=client,
                            production_monitor=FakeMonitor,
                            production_sitemap=FakeSitemap,
                            production_tdm=FakeTDM,
                            connection_probe=SimpleNamespace(sample=lambda: 1),
                        )
                self.assertTrue(stream.closed)

    async def test_direct_urlset_succeeds_and_index_is_rejected(self) -> None:
        urlset = httpx.Response(
            200,
            stream=TrackingStream(
                [b"<urlset><url><loc>https://source-00.example/jobs/1</loc></url></urlset>"]
            ),
        )
        urls, filtered, truncated, requests, decoded = await self.fetch(urlset)
        self.assertEqual(urls, {"https://source-00.example/jobs/1"})
        self.assertEqual((filtered, truncated, requests), (0, False, 1))
        self.assertGreater(decoded, 0)

        index = httpx.Response(200, stream=TrackingStream([b"<sitemapindex/> "]))
        with self.assertRaises(SitemapIndexRejectedError):
            await self.fetch(index)


class ConnectionProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_httpcore_pool_open_waiter_and_close_shape(self) -> None:
        import httpcore

        self.assertEqual(httpcore.__version__, "1.0.9")
        transport = httpx.AsyncHTTPTransport()
        probe = ConnectionProbe(transport)
        self.assertEqual(probe.sample(), 0)
        self.assertEqual(probe.current_waiters, 0)

        class Queued:
            @staticmethod
            def is_queued() -> bool:
                return True

        pool: Any = transport._pool
        pool._connections.append(object())
        pool._requests.append(Queued())
        self.assertEqual(probe.sample(), 1)
        self.assertEqual(probe.current_waiters, 1)
        self.assertEqual(probe.maximum_connections, 1)
        self.assertEqual(probe.maximum_waiters, 1)

        # Remove synthetic state before exercising the real close path.
        pool._connections.clear()
        pool._requests.clear()
        await transport.aclose()
        self.assertEqual(probe.sample(), 0)
        self.assertEqual(probe.current_waiters, 0)

    def test_fixed_pool_highwater_is_bounded_by_pool_limit_not_concurrency(
        self,
    ) -> None:
        concurrency = 2
        retained_idle_plus_active = 12
        final = {
            "accepted": 64,
            "completed": 64,
            "in_flight": 0,
            "connections_open": 0,
            "connection_waiters": 0,
            "maximum_connections": retained_idle_plus_active,
            "max_in_flight": concurrency,
        }

        self.assertTrue(_final_invariants_valid(final, concurrency))
        final["maximum_connections"] = MAX_CONNECTIONS + 1
        self.assertFalse(_final_invariants_valid(final, concurrency))


class ResourceMetricTests(unittest.TestCase):
    def test_reads_current_rss_cgroup_values_and_oom_events(self) -> None:
        with mock.patch(
            "runner.Path.read_text",
            return_value="Name:\tpython\nVmRSS:\t32768 kB\n",
        ):
            self.assertEqual(_current_rss_bytes(), 32 << 20)
        with mock.patch("runner.Path.read_text", return_value="1048576\n"):
            self.assertEqual(_cgroup_value("memory.current"), 1 << 20)
        with mock.patch(
            "runner.Path.read_text",
            return_value="low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n",
        ):
            self.assertEqual(_cgroup_event("oom"), 0)

    def test_missing_negative_and_malformed_resource_metrics_fail_typed_reads(
        self,
    ) -> None:
        with mock.patch("runner.Path.read_text", side_effect=OSError):
            self.assertEqual(_current_rss_bytes(), -1)
            self.assertIsNone(_cgroup_value("memory.current"))
            self.assertIsNone(_cgroup_event("oom"))
        with mock.patch("runner.Path.read_text", return_value="-1\n"):
            self.assertIsNone(_cgroup_value("memory.current"))
        with mock.patch("runner.Path.read_text", return_value="oom nope\n"):
            self.assertIsNone(_cgroup_event("oom"))


class RoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_requires_exactly_one_request_and_wire_attempt(self) -> None:
        jobs = decode_manifest(manifest_bytes()).jobs
        meter = RoundMeter()

        async def fetch(*args, **kwargs):
            del args, kwargs
            job_id = __import__("runner")._current_job.get()
            meter.attempts[job_id].requests += 1
            meter.attempts[job_id].wire_attempts += 1
            await asyncio.sleep(0.01)
            return {f"https://secret.example/jobs/{job_id}"}, 0, False, 1, 10

        with (
            mock.patch("runner._fetch_and_extract", side_effect=fetch),
            mock.patch("runner._current_rss_bytes", return_value=32 << 20),
            mock.patch("runner._open_fds", return_value=12),
        ):
            report = await _run_round(
                jobs,
                round_number=1,
                concurrency=5,
                client=object(),
                meter=meter,
                production_monitor=object(),
                production_sitemap=object(),
                production_tdm=object(),
                connection_probe=SimpleNamespace(sample=lambda: 0),
            )
        encoded = json.dumps(report)
        self.assertEqual(report["status"], "succeeded")
        self.assertEqual(report["max_in_flight"], 5)
        self.assertEqual(report["request_count"], 16)
        self.assertNotIn("secret.example", encoded)

    async def test_error_leaves_lifecycle_zero_and_never_serializes_exception(
        self,
    ) -> None:
        jobs = decode_manifest(manifest_bytes()).jobs

        async def fetch(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("https://secret.example/?token=do-not-emit")

        meter = RoundMeter()
        with mock.patch("runner._fetch_and_extract", side_effect=fetch):
            report = await _run_round(
                jobs,
                round_number=2,
                concurrency=5,
                client=object(),
                meter=meter,
                production_monitor=object(),
                production_sitemap=object(),
                production_tdm=object(),
                connection_probe=SimpleNamespace(sample=lambda: 0),
            )
        encoded = json.dumps(report)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(meter.in_flight, 0)
        self.assertNotIn("secret.example", encoded)
        self.assertNotIn("do-not-emit", encoded)

    async def test_missing_round_rss_and_excess_fds_fail_closed(self) -> None:
        jobs = decode_manifest(manifest_bytes()).jobs
        meter = RoundMeter()

        async def fetch(*args, **kwargs):
            del args, kwargs
            job_id = __import__("runner")._current_job.get()
            meter.attempts[job_id].requests += 1
            meter.attempts[job_id].wire_attempts += 1
            await asyncio.sleep(0.01)
            return {f"https://example.test/jobs/{job_id}"}, 0, False, 1, 10

        with (
            mock.patch("runner._fetch_and_extract", side_effect=fetch),
            mock.patch("runner._current_rss_bytes", return_value=-1),
            mock.patch("runner._open_fds", return_value=257),
        ):
            report = await _run_round(
                jobs,
                round_number=1,
                concurrency=5,
                client=object(),
                meter=meter,
                production_monitor=object(),
                production_sitemap=object(),
                production_tdm=object(),
                connection_probe=SimpleNamespace(sample=lambda: 0),
            )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_kind"], "resource_metrics_invalid")

    async def test_round_deadline_cancels_jobs_and_restores_in_flight_to_zero(
        self,
    ) -> None:
        jobs = decode_manifest(manifest_bytes()).jobs

        async def fetch(*args, **kwargs):
            del args, kwargs
            await asyncio.Event().wait()

        meter = RoundMeter()
        with (
            mock.patch("runner._fetch_and_extract", side_effect=fetch),
            mock.patch("runner.ROUND_TIMEOUT_SECONDS", 0.01),
        ):
            report = await _run_round(
                jobs,
                round_number=1,
                concurrency=5,
                client=object(),
                meter=meter,
                production_monitor=object(),
                production_sitemap=object(),
                production_tdm=object(),
                connection_probe=SimpleNamespace(sample=lambda: 0),
            )
        self.assertEqual(report["error_kind"], "round_deadline")
        self.assertEqual(meter.in_flight, 0)

    async def test_outer_cancellation_joins_active_jobs_before_propagating(
        self,
    ) -> None:
        jobs = decode_manifest(manifest_bytes()).jobs

        async def fetch(*args, **kwargs):
            del args, kwargs
            await asyncio.Event().wait()

        meter = RoundMeter()
        with mock.patch("runner._fetch_and_extract", side_effect=fetch):
            task = asyncio.create_task(
                _run_round(
                    jobs,
                    round_number=1,
                    concurrency=5,
                    client=object(),
                    meter=meter,
                    production_monitor=object(),
                    production_sitemap=object(),
                    production_tdm=object(),
                    connection_probe=SimpleNamespace(sample=lambda: 0),
                )
            )
            while meter.in_flight == 0:
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(meter.in_flight, 0)

    async def test_process_deadline_has_no_eager_dns_fanout_and_is_structural(
        self,
    ) -> None:
        manifest = decode_manifest(manifest_bytes())
        import certifi

        from src.shared import ssrf

        async def never_finishes(*args, **kwargs):
            del args, kwargs
            await asyncio.Event().wait()

        with (
            mock.patch(
                "runner._load_runtime",
                return_value=(httpx, certifi, object(), object(), ssrf, object()),
            ),
            mock.patch("runner._run_round", side_effect=never_finishes),
            mock.patch(
                "src.shared.ssrf.socket.getaddrinfo",
                side_effect=AssertionError("unexpected eager DNS validation"),
            ),
            mock.patch("runner.PROCESS_TIMEOUT_SECONDS", 0.01),
        ):
            report = await run(manifest, "0" * 64, "c5", 5)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_kind"], "process_deadline")
        self.assertEqual(report["rounds"], [])
        self.assertEqual(report["final"]["max_in_flight"], 0)
        self.assertEqual(report["final"]["in_flight"], 0)


if __name__ == "__main__":
    unittest.main()
