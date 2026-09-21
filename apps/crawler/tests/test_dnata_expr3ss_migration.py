"""Contract checks for the bounded Dnata Expr3ss identity repair."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock


def test_dnata_expr3ss_migration_is_scoped_bounded_and_collision_safe(monkeypatch) -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0031_canonicalize_dnata_expr3ss_urls"
    )
    execute = MagicMock()
    monkeypatch.setattr(migration.op, "execute", execute)

    migration.upgrade()

    sql = execute.call_args.args[0]
    assert migration.revision == "0031"
    assert migration.down_revision == "0030"
    assert migration._MAX_CANDIDATES == 500
    assert "candidate_count > 500" in sql
    assert "emirates-group-dnata-au" in sql
    assert "emirates-group-dnata-catering-au" in sql
    assert "^https://dnata[.]expr3ss[.]com/jobDetailsModern" in sql
    assert "^https://dnatacatering[.]expr3ss[.]com/jobDetailsModern" in sql
    assert "canonical.id <> candidate.id" in sql
    assert "found a canonical URL collision" in sql
    assert "SET source_url = regexp_replace" in sql
    assert "&s=250&modern=1" in sql
    assert "updated_at = clock_timestamp()" in sql
    assert "left noncanonical rows" in sql
