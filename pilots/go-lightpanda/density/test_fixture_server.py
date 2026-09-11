from __future__ import annotations

import concurrent.futures
import hashlib
import http.client
import json
import threading
import unittest
from pathlib import Path

import fixture_server as fixture
import python_runner as runner


HERE = Path(__file__).resolve().parent


class FixtureStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workload = runner.load_workload(HERE / "workload.v1.json")

    def test_barrier_requires_c_distinct_origins(self) -> None:
        state = fixture.FixtureState(self.workload, 4)
        routed = [
            state.route(task.origin_id, task.relative_path)
            for task in self.workload.waves[0].tasks[:4]
        ]
        self.assertTrue(all(item is not None for item in routed))
        threads = [
            threading.Thread(target=state.wait_at_barrier, args=(item,))
            for item in routed[:3]
            if item is not None
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(0.02)
            self.assertTrue(thread.is_alive())
        fourth = threading.Thread(target=state.wait_at_barrier, args=(routed[3],))
        fourth.start()
        for thread in [*threads, fourth]:
            thread.join(1)
            self.assertFalse(thread.is_alive())
        self.assertEqual(state.barrier_releases[0], 1)
        self.assertEqual(state.max_global_in_flight, 4)
        self.assertEqual(state.max_per_origin_in_flight, 1)
        for item in routed:
            if item is not None:
                state.finish(item)

    def test_route_rejects_bad_origin_path_and_duplicate_without_echo(self) -> None:
        state = fixture.FixtureState(self.workload, 1)
        task = self.workload.waves[0].tasks[0]
        routed = state.route(task.origin_id, task.relative_path)
        self.assertIsNotNone(routed)
        self.assertIsNone(state.route(task.origin_id, task.relative_path))
        self.assertIsNone(state.route("origin-99", task.relative_path))
        self.assertIsNone(state.route(task.origin_id, "/private/secret"))
        report = state.report()
        self.assertEqual(
            report["unexpected"],
            {"method": 0, "path": 1, "origin": 1, "duplicate": 1, "total": 3},
        )
        wire = json.dumps(report, separators=(",", ":"))
        self.assertNotIn("/private/secret", wire)
        self.assertNotIn("origin-99", wire)
        if routed is not None:
            state.abandon(routed)

    def test_committing_last_response_opens_next_wave(self) -> None:
        state = fixture.FixtureState(self.workload, 1)
        for task in self.workload.waves[0].tasks:
            routed = state.route(task.origin_id, task.relative_path)
            self.assertIsNotNone(routed)
            if routed is not None:
                state.finish(routed)
        next_task = self.workload.waves[1].tasks[0]
        routed = state.route(next_task.origin_id, next_task.relative_path)
        self.assertIsNotNone(routed)
        if routed is not None:
            state.abandon(routed)


class FixtureHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workload = runner.load_workload(HERE / "workload.v1.json")

    @staticmethod
    def _request(port: int, task: runner.Task) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            connection.request(
                "GET",
                task.relative_path,
                headers={"Host": f"{task.origin_id}.bench.test:8080"},
            )
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_serves_exact_bytes_and_emits_closed_complete_transcript(self) -> None:
        original_address = fixture.LISTEN_ADDRESS
        fixture.LISTEN_ADDRESS = ("127.0.0.1", 0)
        state = fixture.FixtureState(self.workload, 4)
        server = fixture.FixtureServer(state)
        fixture.LISTEN_ADDRESS = original_address
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        port = server.server_address[1]
        try:
            for wave in self.workload.waves:
                for offset in (0, 4):
                    tasks = wave.tasks[offset : offset + 4]
                    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                        responses = list(pool.map(lambda task: self._request(port, task), tasks))
                    self.assertEqual(
                        responses,
                        [(task.status, task.response_body) for task in tasks],
                    )
            self.assertTrue(state.done.wait(1))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(1)

        report = state.report()
        self.assertEqual(report["completed"], 16)
        self.assertEqual(report["max_global_in_flight"], 4)
        self.assertEqual(report["max_per_origin_in_flight"], 1)
        self.assertEqual(report["unexpected"], {"method": 0, "path": 0, "origin": 0, "duplicate": 0, "total": 0})
        for wave_report, wave in zip(report["waves"], self.workload.waves, strict=True):
            self.assertEqual(wave_report["task_ids"], [task.id for task in wave.tasks])
            self.assertEqual(
                wave_report["response_sha256"],
                [hashlib.sha256(task.response_body).hexdigest() for task in wave.tasks],
            )
            self.assertTrue(wave_report["barrier_reached"])
        wire = json.dumps(report, separators=(",", ":"))
        for forbidden in ("bench.test", "/density/", "<!doctype", "response_body"):
            self.assertNotIn(forbidden, wire)

    def test_host_authority_requires_explicit_fixture_port(self) -> None:
        original_address = fixture.LISTEN_ADDRESS
        fixture.LISTEN_ADDRESS = ("127.0.0.1", 0)
        state = fixture.FixtureState(self.workload, 1)
        server = fixture.FixtureServer(state)
        fixture.LISTEN_ADDRESS = original_address
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        port = server.server_address[1]
        task = self.workload.waves[0].tasks[0]
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request(
                "GET", task.relative_path, headers={"Host": f"{task.origin_id}.bench.test"}
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(1)
        self.assertEqual(state.report()["unexpected"]["origin"], 1)


if __name__ == "__main__":
    unittest.main()
