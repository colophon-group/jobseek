"""Migrate NW postings from Teamtailor to Welcome to the Jungle identities.

The provider cutover is deployed atomically with this migration: crawler
writers are quiesced, these identities are reconciled, the WTTJ board config is
synced, and only then are workers restarted. For each currently shared job, the
legacy row keeps its UUID when the canonical URL is absent. If a WTTJ monitor
already inserted that URL, the legacy duplicate is tombstoned instead so the
global ``source_url`` uniqueness constraint is never violated. Remaining rows
in NW's exact legacy Teamtailor namespace are no longer authoritative.

Every statement is restricted to ``nw-careers`` and active legacy rows. The
migration is idempotent and updates the CDC clock for every changed row.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-25
"""

from __future__ import annotations

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


_LEGACY_PREFIX = "https://jobs.nw-groupe.com/jobs/"
_CANONICAL_PREFIX = "https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/"

_NW_IDENTITY_MAPPINGS = (
    (
        f"{_LEGACY_PREFIX}7465186-bess-project-manager-italy",
        f"{_CANONICAL_PREFIX}bess-project-manager-italy_milano",
    ),
    (
        f"{_LEGACY_PREFIX}8125397-ingenieur-automaticien-bess-h-f-cdi",
        f"{_CANONICAL_PREFIX}ingenieur-automaticien-bess-h-f-cdi_paris",
    ),
    (
        f"{_LEGACY_PREFIX}5985741-charge-du-suivi-des-financements-h-f-apprentissage",
        f"{_CANONICAL_PREFIX}charge-du-suivi-des-financements-apprentissage_paris_NW_6089Kzx",
    ),
    (
        f"{_LEGACY_PREFIX}8115338-charge-de-financement-h-f-cdi",
        f"{_CANONICAL_PREFIX}charge-de-financement-h-f-cdi_paris",
    ),
    (
        f"{_LEGACY_PREFIX}8113870-ingenieur-qualite-produit-maintenance-n3-h-f-stage",
        f"{_CANONICAL_PREFIX}ingenieur-qualite-produit-maintenance-n3-h-f-stage_paris",
    ),
    (
        f"{_LEGACY_PREFIX}8108098-analyste-foncier-h-f-apprentissage",
        f"{_CANONICAL_PREFIX}analyste-foncier-h-f-apprentissage_lyon",
    ),
    (
        f"{_LEGACY_PREFIX}7580949-purchasing-contract-manager-h-f-cdi",
        f"{_CANONICAL_PREFIX}purchasing-contract-manager-h-f-cdi_paris_NW_qyklLVV",
    ),
    (
        f"{_LEGACY_PREFIX}8011898-senior-erp-project-manager-h-f-cdi",
        f"{_CANONICAL_PREFIX}senior-erp-migration-project-manager-h-f-cdi_paris",
    ),
    (
        f"{_LEGACY_PREFIX}8010487-rnw-stage-chef-de-projet-marche-h-f",
        f"{_CANONICAL_PREFIX}rnw-stage-chef-de-projet-marche-h-f_paris_NW_VdkN6eN",
    ),
)

_IDENTITY_VALUES = ",\n".join(
    f"        ('{legacy_url}', '{canonical_url}')"
    for legacy_url, canonical_url in _NW_IDENTITY_MAPPINGS
)

_MIGRATE_NW_PROVIDER_IDENTITIES = f"""
DO $jobseek$
DECLARE
    nw_board_count integer;
BEGIN
    SELECT count(*)
    INTO nw_board_count
    FROM job_board
    WHERE board_slug = 'nw-careers';

    IF nw_board_count > 1 THEN
        RAISE EXCEPTION 'NW provider cutover found ambiguous nw-careers ownership';
    END IF;

    IF nw_board_count = 1 AND EXISTS (
        SELECT 1
        FROM job_posting AS canonical
        JOIN (VALUES
{_IDENTITY_VALUES}
        ) AS identity_map (legacy_url, canonical_url)
          ON canonical.source_url = identity_map.canonical_url
        WHERE canonical.board_id IS DISTINCT FROM (
            SELECT id FROM job_board WHERE board_slug = 'nw-careers'
        )
    ) THEN
        RAISE EXCEPTION 'NW provider cutover found foreign canonical URL ownership';
    END IF;
END
$jobseek$;

WITH identity_map (legacy_url, canonical_url) AS (
    VALUES
{_IDENTITY_VALUES}
)
UPDATE job_posting AS legacy
SET is_active = false,
    next_scrape_at = NULL,
    updated_at = now()
FROM job_board AS board,
     identity_map
WHERE legacy.board_id = board.id
  AND board.board_slug = 'nw-careers'
  AND legacy.source_url = identity_map.legacy_url
  AND legacy.is_active = true
  AND EXISTS (
      SELECT 1
      FROM job_posting AS canonical
      WHERE canonical.source_url = identity_map.canonical_url
        AND canonical.board_id = board.id
        AND canonical.id <> legacy.id
  );

WITH identity_map (legacy_url, canonical_url) AS (
    VALUES
{_IDENTITY_VALUES}
)
UPDATE job_posting AS legacy
SET source_url = identity_map.canonical_url,
    updated_at = now()
FROM job_board AS board,
     identity_map
WHERE legacy.board_id = board.id
  AND board.board_slug = 'nw-careers'
  AND legacy.source_url = identity_map.legacy_url
  AND legacy.is_active = true
  AND NOT EXISTS (
      SELECT 1
      FROM job_posting AS canonical
      WHERE canonical.source_url = identity_map.canonical_url
        AND canonical.board_id = board.id
        AND canonical.id <> legacy.id
  );

UPDATE job_posting AS posting
SET is_active = false,
    next_scrape_at = NULL,
    updated_at = now()
FROM job_board AS board
WHERE posting.board_id = board.id
  AND board.board_slug = 'nw-careers'
  AND posting.source_url LIKE '{_LEGACY_PREFIX}%'
  AND posting.is_active = true
"""


def upgrade() -> None:
    op.execute(_MIGRATE_NW_PROVIDER_IDENTITIES)


def downgrade() -> None:
    # Source URL identity changes and later provider observations cannot be
    # distinguished safely, so the data migration is intentionally one-way.
    pass
