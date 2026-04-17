"""Tests for ``src.processing.r2_stage`` — the hash stability contract.

These tests guard the invariant that ``_compute_r2_hash`` is insensitive
to upstream non-determinism that does not represent a real content
change. Before PR #2223, ``_deep_sort`` silently left list order alone
despite its docstring, so any posting whose source API returned
locations (or any list-of-strings metadata field) in a different order
across scrapes re-uploaded on every scrape, even though the hash was
supposed to be content-stable.
"""

from __future__ import annotations

from src.processing.r2_stage import (
    _compute_r2_hash,
    _compute_r2_hash_legacy,
    _deep_sort,
    _deep_sort_legacy,
    _stage_r2_pending,
)


class TestDeepSort:
    def test_dicts_sorted_by_key(self):
        assert _deep_sort({"b": 1, "a": 2}) == {"a": 2, "b": 1}

    def test_list_of_strings_is_sorted(self):
        # The documented behaviour: upstream list ordering is often
        # non-deterministic (Accenture, Google, Workday), and order
        # on a set-like field is not a content change.
        assert _deep_sort(["NYC", "LA", "Chicago"]) == ["Chicago", "LA", "NYC"]

    def test_empty_list_unchanged(self):
        assert _deep_sort([]) == []

    def test_nested_lists_keep_order(self):
        # A list whose elements are themselves lists is NOT a
        # set-of-strings, so we don't reshuffle it.
        nested = [["b", "a"], ["d", "c"]]
        assert _deep_sort(nested) == [["a", "b"], ["c", "d"]]

    def test_mixed_type_list_keeps_order(self):
        # Heterogeneous lists — e.g. structured pay-band dicts
        # interleaved with strings — don't get reordered, since
        # Python's sort can't compare arbitrary types.
        mixed = [{"min": 50}, "note"]
        assert _deep_sort(mixed) == [{"min": 50}, "note"]

    def test_nested_dict_lists_sorted(self):
        # metadata.tags is the motivating case: set-like, surfaces
        # into the hash input through merged_extras.
        payload = {"metadata": {"tags": ["python", "golang", "rust"]}}
        assert _deep_sort(payload) == {"metadata": {"tags": ["golang", "python", "rust"]}}


class TestLegacyDeepSort:
    def test_matches_pre_fix_behaviour(self):
        # The legacy helper must reproduce exactly what ``_deep_sort``
        # did before the fix, otherwise the migration shim can't
        # recognise a pre-fix stored hash as "still current content".
        assert _deep_sort_legacy(["b", "a"]) == ["b", "a"]
        assert _deep_sort_legacy({"b": 1, "a": 2}) == {"a": 2, "b": 1}
        assert _deep_sort_legacy({"tags": ["b", "a"]}) == {"tags": ["b", "a"]}


class TestComputeR2Hash:
    _BASE_EXTRAS = {
        "title": "Senior Engineer",
        "metadata": {"team": "platform"},
        "raw_employment_type": "full_time",
    }

    def test_stable_across_location_reorderings(self):
        # Regression: Accenture and similar boards return the same
        # location set in different orders between calls — after the
        # _deep_sort fix this must not flip the hash.
        a = dict(self._BASE_EXTRAS, locations=["London", "New York", "Remote"])
        b = dict(self._BASE_EXTRAS, locations=["Remote", "London", "New York"])
        assert _compute_r2_hash("desc", a) == _compute_r2_hash("desc", b)

    def test_stable_across_metadata_list_reorderings(self):
        # Same invariant for list-of-string metadata fields like
        # tags / categories / departments.
        a = {"metadata": {"tags": ["python", "rust", "golang"]}}
        b = {"metadata": {"tags": ["golang", "python", "rust"]}}
        assert _compute_r2_hash("desc", a) == _compute_r2_hash("desc", b)

    def test_different_description_produces_different_hash(self):
        h1 = _compute_r2_hash("first", self._BASE_EXTRAS)
        h2 = _compute_r2_hash("second", self._BASE_EXTRAS)
        assert h1 != h2

    def test_volatile_fields_excluded(self):
        # valid_through / expiration_date change constantly upstream and
        # must not affect the hash — this contract predates the fix.
        base = dict(self._BASE_EXTRAS, valid_through="2026-12-31")
        shifted = dict(self._BASE_EXTRAS, valid_through="2026-11-30")
        assert _compute_r2_hash("d", base) == _compute_r2_hash("d", shifted)

    def test_legacy_sensitive_to_list_order(self):
        # The legacy hash MUST still flip on list reorderings — that's
        # the whole reason the migration shim exists. If this were
        # order-insensitive, every stored hash would already be
        # order-stable and there'd be no noise to fix.
        a = dict(self._BASE_EXTRAS, locations=["London", "New York"])
        b = dict(self._BASE_EXTRAS, locations=["New York", "London"])
        assert _compute_r2_hash_legacy("d", a) != _compute_r2_hash_legacy("d", b)

    def test_new_and_legacy_agree_when_lists_already_sorted(self):
        # When upstream happens to emit already-sorted lists, the two
        # algorithms produce identical output — so most postings will
        # migrate their stored hash without ever triggering the "legacy
        # matched" branch of the UPSERT.
        extras = dict(self._BASE_EXTRAS, locations=["London", "New York", "Remote"])
        assert _compute_r2_hash("d", extras) == _compute_r2_hash_legacy("d", extras)


class TestStageR2Pending:
    def test_returns_four_tuple_with_both_hashes(self):
        staged = _stage_r2_pending(
            title="Engineer",
            description="<p>desc</p>",
            language="en",
            locations=["LA", "NYC"],
            localizations=None,
            extras=None,
            metadata=None,
            date_posted=None,
            base_salary=None,
            employment_type=None,
            job_location_type=None,
        )
        assert staged is not None
        html, locale, new_hash, legacy_hash = staged
        assert html == "<p>desc</p>"
        assert locale == "en"
        assert isinstance(new_hash, int)
        assert isinstance(legacy_hash, int)

    def test_short_circuits_on_hash_match(self):
        kwargs = dict(
            title="Engineer",
            description="<p>desc</p>",
            language="en",
            locations=["LA", "NYC"],
            localizations=None,
            extras=None,
            metadata=None,
            date_posted=None,
            base_salary=None,
            employment_type=None,
            job_location_type=None,
        )
        first = _stage_r2_pending(**kwargs)
        assert first is not None
        _, _, new_hash, _ = first
        # A subsequent call that already knows the current new_hash
        # short-circuits to None (the scrape pipeline skips the UPSERT
        # entirely in that case).
        assert _stage_r2_pending(**kwargs, current_hash=new_hash) is None

    def test_returns_tuple_when_only_legacy_matches(self):
        # Pre-migration state: the stored hash is the legacy value.
        # ``_stage_r2_pending`` must NOT short-circuit here — it has to
        # return the (new, legacy) pair so the UPSERT can see the
        # legacy match and skip the R2 re-upload. Short-circuiting
        # on legacy would keep the stored hash pinned to the old value
        # forever and never complete the migration.
        kwargs = dict(
            title="Engineer",
            description="<p>desc</p>",
            language="en",
            # Non-sorted order — this is where new vs legacy diverge.
            locations=["NYC", "LA"],
            localizations=None,
            extras=None,
            metadata=None,
            date_posted=None,
            base_salary=None,
            employment_type=None,
            job_location_type=None,
        )
        staged = _stage_r2_pending(**kwargs)
        assert staged is not None
        _, _, new_hash, legacy_hash = staged
        assert new_hash != legacy_hash  # confirms the test exercises divergence

        result = _stage_r2_pending(**kwargs, current_hash=legacy_hash)
        assert result is not None, "must not short-circuit on legacy-only match"
        _, _, got_new, got_legacy = result
        assert got_new == new_hash
        assert got_legacy == legacy_hash

    def test_returns_none_for_empty_description(self):
        # No description → nothing to stage. Was true before the fix,
        # still true after.
        assert (
            _stage_r2_pending(
                title="Engineer",
                description=None,
                language="en",
                locations=None,
                localizations=None,
                extras=None,
                metadata=None,
                date_posted=None,
                base_salary=None,
                employment_type=None,
                job_location_type=None,
            )
            is None
        )
