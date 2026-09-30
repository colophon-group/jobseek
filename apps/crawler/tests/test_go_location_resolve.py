"""Location ownership, frozen semantics, and persistent-index recovery."""

from __future__ import annotations

import json
import multiprocessing
import subprocess
from pathlib import Path

import pytest

from src.core.location_resolve import LocationResolver
from src.runtime.location_go import GoLocationIndex

MODULE = Path(__file__).resolve().parents[1] / "go/job-enrichment"
pytestmark = pytest.mark.timeout(180)


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    path = tmp_path_factory.mktemp("go-location") / "resolver"
    subprocess.run(
        ["go", "build", "-o", str(path), "./cmd/location"],
        cwd=MODULE,
        capture_output=True,
        check=True,
    )
    return str(path)


@pytest.fixture
def native(binary, monkeypatch):
    monkeypatch.setenv("JOB_ENRICHMENT_ENGINE", "go")
    original = GoLocationIndex.__init__

    def init(self):
        original(self)
        self.client.binary = binary

    monkeypatch.setattr(GoLocationIndex, "__init__", init)
    corpus = json.loads((MODULE / "testdata/python_location.json").read_text())
    signature, fixture = next(iter(corpus["fixtures"].items()))
    resolver = LocationResolver()
    resolver._init_db()
    db = resolver._db
    for table, rows, marks in [
        ("entry", fixture["entries"], "?,?,?,?,?"),
        ("name_index", fixture["names"], "?,?"),
        ("display_name", fixture["display"], "?,?"),
    ]:
        db.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
    db.executescript("CREATE INDEX idx_name ON name_index(name)")
    db.commit()
    resolver._loaded = True
    resolver._tracking = True
    yield resolver, corpus, signature
    db.close()
    resolver._go.close()


def test_native_entire_location_oracle_without_python_matching(native, monkeypatch):
    resolver, corpus, signature = native

    def reject(*args, **kwargs):
        raise AssertionError("native location resolution called Python matching")

    monkeypatch.setattr(resolver, "_resolve_one", reject)
    for case in corpus["cases"]:
        assert case["fixture"] == signature
        resolver._tracking = case["tracking"]
        resolver._negative = set(case["negative"])
        resolver._misses = set(case["misses_before"])
        result = resolver.resolve(case["raw"], case["fallback"], case["language"])
        assert [
            {"location_id": r.location_id, "location_type": r.location_type} for r in result
        ] == case["expected"]
        assert sorted(resolver._misses) == case["lookup_misses"]
        assert [
            {"raw_value": a, "sample_value": b} for a, b in resolver.drain_location_misses()
        ] == case["location_misses"]


def test_index_backfill_and_negative_cache_survive_process_restart(native, monkeypatch):
    resolver, _, _ = native
    assert resolver.resolve(["UnmatchedXYZ"]) == []
    resolver._negative.update(resolver._misses)
    resolver._misses.clear()
    # Model the existing-pool backfill commit, including name variants.
    resolver._db.executemany(
        "INSERT INTO name_index VALUES (?,?)",
        [(name, 2657896) for name in resolver._name_variants("the zürich city")],
    )
    resolver._db.commit()
    assert resolver.resolve(["The Zürich City"])[0].location_id == 2657896
    assert resolver.resolve(["UnmatchedXYZ"]) == []
    assert resolver._misses == set()
    resolver._go.client.close()
    assert resolver.resolve(["The Zürich City"])[0].location_id == 2657896
    assert resolver.resolve(["UnmatchedXYZ"]) == []
    assert resolver._misses == set()


def test_native_ancestors_display_and_cycle_bound(native, monkeypatch):
    resolver, _, _ = native
    resolver._db.execute("INSERT INTO display_name VALUES (?,?)", (2657896, "Zürich"))
    resolver._db.executemany(
        "INSERT INTO entry VALUES (?,?,?,?,?)",
        [(99991, 99992, "region", 0, ""), (99992, 99991, "macro", 0, "")],
    )
    resolver._db.commit()
    monkeypatch.setattr(resolver, "_get_entry", lambda *_: pytest.fail("Python ancestor lookup"))
    assert resolver.display_name(2657896) == "Zürich"
    assert resolver.display_name(99990) is None
    assert set(resolver.get_ancestor_ids(99991)) == {99991, 99992}
    assert resolver.get_ancestor_ids(99990) == [99990]


def test_missing_index_fails_without_empty_success_or_python_fallback(native, monkeypatch):
    resolver, _, _ = native
    resolver.resolve(["Zurich"])
    resolver._go.client.close()
    resolver._go.index_path.unlink()
    monkeypatch.setattr(resolver, "_resolve_one", lambda *_: pytest.fail("Python fallback"))
    with pytest.raises(RuntimeError, match="exited before its response"):
        resolver.resolve(["Zurich"])
    assert resolver._go.client.proc is None


def test_fork_keeps_parent_process_and_private_index(native):
    resolver, _, _ = native
    resolver.resolve(["Zurich"])
    parent = resolver._go.client.proc
    directory = resolver._go.directory
    context = multiprocessing.get_context("fork")
    output, child_end = context.Pipe(duplex=False)

    def child():
        values = resolver.resolve(["Zurich"])
        child_end.send((resolver._go.client.proc.pid, values[0].location_id))
        resolver._go.close()
        child_end.close()

    process = context.Process(target=child)
    process.start()
    child_end.close()
    try:
        assert output.poll(10)
        pid, location_id = output.recv()
        process.join(5)
        assert pid != parent.pid and location_id == 2657896
        assert process.exitcode == 0
        assert parent.poll() is None and directory.is_dir()
        assert resolver.resolve(["Zurich"])[0].location_id == 2657896
    finally:
        output.close()
        if process.is_alive():
            process.kill()
            process.join()


@pytest.mark.asyncio
async def test_native_load_and_noncore_backfill_keep_existing_pool_budget(native):
    class Connection:
        async def fetch(self, sql, *args):
            if "WHERE lower(name)" in sql:
                chunks.append(list(args[0]))
                return [{"location_id": 2, "name": "مدينة"}] if "مدينة" in args[0] else []
            if "WHERE locale = 'en'" in sql:
                return [{"location_id": 2, "name": "Example City", "is_display": True}]
            if "FROM location_name" in sql:
                return [{"location_id": 1, "name": "example country"}]
            return [
                {
                    "id": 1,
                    "parent_id": None,
                    "type": "country",
                    "population": 100000,
                    "languages": ["en"],
                },
                {"id": 2, "parent_id": 1, "type": "city", "population": 1000, "languages": ["en"]},
            ]

    class Acquired:
        async def __aenter__(self):
            pool.active += 1
            assert pool.active == 1
            return Connection()

        async def __aexit__(self, *args):
            pool.active -= 1

    class Pool:
        active = 0

        def acquire(self):
            return Acquired()

    chunks = []
    pool = Pool()
    resolver = LocationResolver()
    try:
        await resolver.load(pool)
        assert resolver.entry_count == 2
        assert resolver.display_name(2) == "Example City"
        assert resolver.get_ancestor_ids(2) == [2, 1]
        assert resolver.resolve(["مدينة"]) == []
        resolver._misses.update(f"missing-{i}" for i in range(548))
        assert await resolver.backfill_misses()
        assert len(chunks) == 2 and all(len(chunk) <= 500 for chunk in chunks)
        assert resolver.resolve(["مدينة"])[0].location_id == 2
        assert resolver.resolve(["missing-0"]) == []
        assert resolver._misses == set()
        assert not await resolver.backfill_misses()
        assert pool.active == 0 and len(chunks) == 2
    finally:
        if resolver._db is not None:
            resolver._db.close()
        if resolver._go is not None:
            resolver._go.close()
