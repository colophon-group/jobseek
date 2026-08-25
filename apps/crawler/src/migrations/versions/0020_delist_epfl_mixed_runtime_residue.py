"""Delist exact EPFL postings admitted by the mixed-runtime incident.

The deployment-order incident tracked in #7996 briefly ran two EPFL inline
monitor contracts against the same boards. Ten synthetic identities that are
not present in the corrected sources remained active because the later
blast-radius guard correctly refused to treat the unexpectedly large diff as
authoritative.

This one-shot repair deliberately does not weaken or bypass that guard. It
matches only the ten observed ``(board_slug, source_url)`` pairs and only rows
that are still active. Updating ``updated_at`` makes each tombstone visible to
the normal CDC exporter; clearing ``next_scrape_at`` prevents stale scrape work.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-25
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


_DELIST_EPFL_INCIDENT_POSTINGS = """
WITH incident_posting (board_slug, source_url) AS (
    VALUES
        (
            'epfl-hqc',
            'https://www.epfl.ch/labs/hqc/open-positions/?_jid=back-to-top-9641fb'
        ),
        (
            'epfl-hqc',
            'https://www.epfl.ch/labs/hqc/open-positions/?_jid=fabrication-and-characterization-granular-aluminum-987304'
        ),
        (
            'epfl-hqc',
            'https://www.epfl.ch/labs/hqc/open-positions/?_jid=fabrication-and-characterization-of-quantum-dots-i-0ecb86'
        ),
        (
            'epfl-hqc',
            'https://www.epfl.ch/labs/hqc/open-positions/?_jid=implementation-of-critical-qubit-readout-858a4e'
        ),
        (
            'epfl-hqc',
            'https://www.epfl.ch/labs/hqc/open-positions/?_jid=multimode-quantum-electrodynamics-with-atom-photon-e79156'
        ),
        (
            'epfl-hqc',
            'https://www.epfl.ch/labs/hqc/open-positions/?_jid=skip-to-content-96272c'
        ),
        (
            'epfl-lfim',
            'https://www.epfl.ch/labs/lfim/openings/?_jid=covalent-organic-frameworks-for-gold-recovery-from-810fc6'
        ),
        (
            'epfl-lfim',
            'https://www.epfl.ch/labs/lfim/openings/?_jid=development-of-sorbents-for-direct-air-capture-of--d63fcd'
        ),
        (
            'epfl-lfim',
            'https://www.epfl.ch/labs/lfim/openings/?_jid=optimizing-adsorbent-materials-for-gas-separations-c9fe69'
        ),
        (
            'epfl-lfim',
            'https://www.epfl.ch/labs/lfim/openings/?_jid=precious-metal-recovery-from-industrial-waste-stre-b68aae'
        )
)
UPDATE job_posting AS posting
SET is_active = false,
    next_scrape_at = NULL,
    updated_at = now()
FROM job_board AS board,
     incident_posting AS incident
WHERE posting.board_id = board.id
  AND board.board_slug = incident.board_slug
  AND posting.source_url = incident.source_url
  AND posting.is_active = true
"""


def upgrade() -> None:
    op.execute(_DELIST_EPFL_INCIDENT_POSTINGS)


def downgrade() -> None:
    # The migration cannot safely distinguish a later legitimate reappearance
    # of an identity, so reactivation is intentionally left to a live monitor.
    pass
