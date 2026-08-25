"""Regression contract for the bounded EPFL incident cleanup."""

from __future__ import annotations

import importlib
import re
from unittest.mock import MagicMock

_EXPECTED_TARGETS = {
    (
        "epfl-hqc",
        "https://www.epfl.ch/labs/hqc/open-positions/?_jid=back-to-top-9641fb",
    ),
    (
        "epfl-hqc",
        "https://www.epfl.ch/labs/hqc/open-positions/"
        "?_jid=fabrication-and-characterization-granular-aluminum-987304",
    ),
    (
        "epfl-hqc",
        "https://www.epfl.ch/labs/hqc/open-positions/"
        "?_jid=fabrication-and-characterization-of-quantum-dots-i-0ecb86",
    ),
    (
        "epfl-hqc",
        "https://www.epfl.ch/labs/hqc/open-positions/"
        "?_jid=implementation-of-critical-qubit-readout-858a4e",
    ),
    (
        "epfl-hqc",
        "https://www.epfl.ch/labs/hqc/open-positions/"
        "?_jid=multimode-quantum-electrodynamics-with-atom-photon-e79156",
    ),
    (
        "epfl-hqc",
        "https://www.epfl.ch/labs/hqc/open-positions/?_jid=skip-to-content-96272c",
    ),
    (
        "epfl-lfim",
        "https://www.epfl.ch/labs/lfim/openings/"
        "?_jid=covalent-organic-frameworks-for-gold-recovery-from-810fc6",
    ),
    (
        "epfl-lfim",
        "https://www.epfl.ch/labs/lfim/openings/"
        "?_jid=development-of-sorbents-for-direct-air-capture-of--d63fcd",
    ),
    (
        "epfl-lfim",
        "https://www.epfl.ch/labs/lfim/openings/"
        "?_jid=optimizing-adsorbent-materials-for-gas-separations-c9fe69",
    ),
    (
        "epfl-lfim",
        "https://www.epfl.ch/labs/lfim/openings/"
        "?_jid=precious-metal-recovery-from-industrial-waste-stre-b68aae",
    ),
}


def test_migration_delists_only_the_ten_exact_epfl_incident_identities() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0020_delist_epfl_mixed_runtime_residue"
    )
    sql = migration._DELIST_EPFL_INCIDENT_POSTINGS
    targets = re.findall(r"\(\s*'([^']+)',\s*'([^']+)'\s*\)", sql)

    assert migration.revision == "0020"
    assert migration.down_revision == "0019"
    assert set(targets) == _EXPECTED_TARGETS
    assert len(targets) == 10
    assert "posting.board_id = board.id" in sql
    assert "board.board_slug = incident.board_slug" in sql
    assert "posting.source_url = incident.source_url" in sql
    assert "posting.is_active = true" in sql
    assert "SET is_active = false" in sql
    assert "next_scrape_at = NULL" in sql
    assert "updated_at = now()" in sql
    assert " LIKE " not in sql
    assert " SIMILAR TO " not in sql
    assert " ~ " not in sql

    execute = MagicMock()
    original_op = migration.op
    migration.op = MagicMock(execute=execute)
    try:
        migration.upgrade()
        migration.downgrade()
    finally:
        migration.op = original_op
    execute.assert_called_once_with(sql)
