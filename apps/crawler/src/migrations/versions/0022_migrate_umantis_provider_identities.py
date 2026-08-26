"""Migrate Umantis locale URLs to stable provider-ID description routes.

Umantis listings expose locale aliases such as ``/Description/1`` and
``/Description/3`` for one numeric vacancy. The suffix-free
``/Vacancies/{id}/Description`` route redirects to an available locale, so it
is both stable identity and a viable scrape URL.

Crawler writers are quiesced before Alembic runs. This migration updates every
historical posting for the seven existing Umantis boards in place, preserving
posting UUIDs, activity, scrape history, and content. It refuses unknown board
ownership, unexpected URLs, duplicate provider identities, foreign canonical
ownership, or a mismatched durable receipt. Updating ``updated_at`` lets the
ordinary CDC exporter carry the URL change downstream without tombstones.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-26
"""

from __future__ import annotations

import re

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


# Reuse the sync-preserved runtime receipt slot. CSV sync deliberately carries
# this key across metadata replacement, so the migration cannot be re-armed by
# the deploy step that immediately follows Alembic.
_RECEIPT_KEY = "_identity_migration_receipt"
_MIGRATION_ID = "umantis-stable-description-v1"

# Exact pre-deploy registry contracts. University of Neuchatel is added by the
# same release after migrations run, so it starts directly on stable URLs and
# intentionally has no historical rows to migrate.
_UMANTIS_BOARD_CONTRACTS = (
    (
        "bobst-global",
        "bobst",
        "https://jobs.bobst.com/Jobs/All",
        "https://recruitingapp-2882.umantis.com",
    ),
    (
        "bucherer-careers-umantis",
        "bucherer",
        "https://recruitingapp-2840.umantis.com/Jobs/All",
        "https://recruitingapp-2840.umantis.com",
    ),
    (
        "canton-neuchatel-careers",
        "canton-neuchatel",
        "https://recruitingapp-2702.umantis.com/Jobs/All",
        "https://recruitingapp-2702.umantis.com",
    ),
    (
        "fhgr-careers",
        "fhgr",
        "https://recruitingapp-2865.umantis.com/Jobs/All",
        "https://recruitingapp-2865.umantis.com",
    ),
    (
        "j-safra-sarasin-careers",
        "j-safra-sarasin",
        "https://jsafrasarasin.umantis.com/Jobs/All",
        "https://jsafrasarasin.umantis.com",
    ),
    (
        "lindt-spruengli-careers",
        "lindt-spruengli",
        "https://www.lindt-spruengli.com/careers/vacancies",
        "https://recruitingapp-1619.umantis.com",
    ),
    (
        "ruag-main",
        "ruag",
        "https://recruiting.ruag.ch/Jobs/All",
        "https://recruitingapp-2514.umantis.com",
    ),
)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_CONTRACT_VALUES = ",\n".join(
    "        ("
    + ", ".join(
        _literal(value)
        for value in (
            board_slug,
            company_slug,
            board_url,
            source_base,
            rf"^{re.escape(source_base)}/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$",
            rf"^{re.escape(source_base)}/Vacancies/[1-9][0-9]*/Description$",
        )
    )
    + ")"
    for board_slug, company_slug, board_url, source_base in _UMANTIS_BOARD_CONTRACTS
)


_MIGRATE_UMANTIS_PROVIDER_IDENTITIES = f"""
DO $jobseek$
BEGIN
    -- A brand-new database has no CSV-synced boards when Alembic runs. Allow
    -- exactly that empty state; once any historical contract board exists,
    -- require the complete seven-board production registry.
    IF (
        SELECT count(board.id)
        FROM (VALUES
{_CONTRACT_VALUES}
        ) AS contract (
            board_slug, company_slug, board_url, source_base,
            legacy_pattern, canonical_pattern
        )
        LEFT JOIN job_board AS board
          ON board.board_slug = contract.board_slug
    ) NOT IN (0, {len(_UMANTIS_BOARD_CONTRACTS)})
    OR EXISTS (
        SELECT 1
        FROM (VALUES
{_CONTRACT_VALUES}
        ) AS contract (
            board_slug, company_slug, board_url, source_base,
            legacy_pattern, canonical_pattern
        )
        LEFT JOIN job_board AS board
          ON board.board_slug = contract.board_slug
        LEFT JOIN company AS owner
          ON owner.id = board.company_id
        WHERE board.id IS NOT NULL
          AND (owner.slug IS DISTINCT FROM contract.company_slug
           OR board.board_url IS DISTINCT FROM contract.board_url
           OR board.crawler_type IS DISTINCT FROM 'umantis')
    ) THEN
        RAISE EXCEPTION 'Umantis identity migration board contract mismatch';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (VALUES
{_CONTRACT_VALUES}
        ) AS contract (
            board_slug, company_slug, board_url, source_base,
            legacy_pattern, canonical_pattern
        )
        JOIN job_board AS board
          ON board.board_slug = contract.board_slug
        CROSS JOIN LATERAL (
            SELECT board.metadata -> '{_RECEIPT_KEY}' AS value
        ) AS receipt
        CROSS JOIN LATERAL (
            SELECT count(*) AS total_count
            FROM job_posting
            WHERE board_id = board.id
        ) AS current_state
        WHERE receipt.value IS NOT NULL
          AND (
              jsonb_typeof(receipt.value) IS DISTINCT FROM 'object'
              OR jsonb_typeof(receipt.value -> 'id') IS DISTINCT FROM 'string'
              OR receipt.value ->> 'id' IS DISTINCT FROM '{_MIGRATION_ID}'
              OR jsonb_typeof(receipt.value -> 'version') IS DISTINCT FROM 'number'
              OR receipt.value ->> 'version' IS DISTINCT FROM '1'
              OR jsonb_typeof(receipt.value -> 'completed_at') IS DISTINCT FROM 'string'
              OR COALESCE(receipt.value ->> 'completed_at', '')
                 !~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}T.+(Z|[+-][0-9]{{2}}:[0-9]{{2}})$'
              OR jsonb_typeof(receipt.value -> 'migrated_count') IS DISTINCT FROM 'number'
              OR (receipt.value ->> 'migrated_count') !~ '^[0-9]+$'
              OR jsonb_typeof(receipt.value -> 'total_count') IS DISTINCT FROM 'number'
              OR (receipt.value ->> 'total_count') !~ '^[0-9]+$'
              OR receipt.value -> 'migrated_count' > receipt.value -> 'total_count'
              OR receipt.value -> 'total_count' > to_jsonb(current_state.total_count)
              OR ARRAY(
                  SELECT key
                  FROM jsonb_object_keys(receipt.value) AS key
                  ORDER BY key
              ) IS DISTINCT FROM ARRAY[
                  'completed_at', 'id', 'migrated_count', 'total_count', 'version'
              ]
          )
    ) THEN
        RAISE EXCEPTION 'Umantis identity migration receipt mismatch';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (VALUES
{_CONTRACT_VALUES}
        ) AS contract (
            board_slug, company_slug, board_url, source_base,
            legacy_pattern, canonical_pattern
        )
        JOIN job_board AS board
          ON board.board_slug = contract.board_slug
        JOIN job_posting AS posting
          ON posting.board_id = board.id
        WHERE posting.source_url !~ contract.legacy_pattern
          AND posting.source_url !~ contract.canonical_pattern
    ) THEN
        RAISE EXCEPTION 'Umantis identity migration found an unexpected board URL';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (VALUES
{_CONTRACT_VALUES}
        ) AS contract (
            board_slug, company_slug, board_url, source_base,
            legacy_pattern, canonical_pattern
        )
        JOIN job_board AS board
          ON board.board_slug = contract.board_slug
        JOIN job_posting AS posting
          ON posting.board_id = board.id
        GROUP BY board.id,
                 regexp_replace(
                     posting.source_url,
                     '/Description/[1-9][0-9]*$',
                     '/Description'
                 )
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Umantis identity migration found duplicate provider identities';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (VALUES
{_CONTRACT_VALUES}
        ) AS contract (
            board_slug, company_slug, board_url, source_base,
            legacy_pattern, canonical_pattern
        )
        JOIN job_board AS board
          ON board.board_slug = contract.board_slug
        JOIN job_posting AS legacy
          ON legacy.board_id = board.id
         AND legacy.source_url ~ contract.legacy_pattern
        JOIN job_posting AS canonical
          ON canonical.source_url = regexp_replace(
              legacy.source_url,
              '/Description/[1-9][0-9]*$',
              '/Description'
          )
         AND canonical.board_id IS DISTINCT FROM board.id
    ) THEN
        RAISE EXCEPTION 'Umantis identity migration found foreign canonical URL ownership';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (VALUES
{_CONTRACT_VALUES}
        ) AS contract (
            board_slug, company_slug, board_url, source_base,
            legacy_pattern, canonical_pattern
        )
        JOIN job_board AS board
          ON board.board_slug = contract.board_slug
         AND board.metadata ? '{_RECEIPT_KEY}'
        JOIN job_posting AS posting
          ON posting.board_id = board.id
         AND posting.source_url ~ contract.legacy_pattern
    ) THEN
        RAISE EXCEPTION 'Umantis identity migration found legacy URLs after its receipt';
    END IF;
END
$jobseek$;

WITH contract (
    board_slug, company_slug, board_url, source_base,
    legacy_pattern, canonical_pattern
) AS (
    VALUES
{_CONTRACT_VALUES}
), owned_board AS MATERIALIZED (
    SELECT board.id, board.metadata, contract.legacy_pattern
    FROM contract
    JOIN job_board AS board
      ON board.board_slug = contract.board_slug
), before_state AS MATERIALIZED (
    SELECT owned_board.id AS board_id,
           count(posting.id) AS total_count,
           count(posting.id) FILTER (
               WHERE posting.source_url ~ owned_board.legacy_pattern
           ) AS legacy_count
    FROM owned_board
    LEFT JOIN job_posting AS posting
      ON posting.board_id = owned_board.id
    GROUP BY owned_board.id
), migrated AS (
    UPDATE job_posting AS posting
    SET source_url = regexp_replace(
            posting.source_url,
            '/Description/[1-9][0-9]*$',
            '/Description'
        ),
        updated_at = clock_timestamp()
    FROM owned_board
    WHERE posting.board_id = owned_board.id
      AND posting.source_url ~ owned_board.legacy_pattern
    RETURNING posting.board_id
), migration_count AS MATERIALIZED (
    SELECT board_id, count(*) AS migrated_count
    FROM migrated
    GROUP BY board_id
), receipt AS (
    UPDATE job_board AS board
    SET metadata = COALESCE(board.metadata, '{{}}'::jsonb)
                   || jsonb_build_object(
                        '{_RECEIPT_KEY}',
                        jsonb_build_object(
                            'id', '{_MIGRATION_ID}',
                            'version', 1,
                            'completed_at', clock_timestamp(),
                            'migrated_count', before_state.legacy_count,
                            'total_count', before_state.total_count
                        )
                    ),
        updated_at = clock_timestamp()
    FROM before_state
    LEFT JOIN migration_count
      ON migration_count.board_id = before_state.board_id
    WHERE board.id = before_state.board_id
      AND NOT (COALESCE(board.metadata, '{{}}'::jsonb) ? '{_RECEIPT_KEY}')
      AND COALESCE(migration_count.migrated_count, 0) = before_state.legacy_count
    RETURNING board.id
)
SELECT count(*) FROM receipt;
"""


def upgrade() -> None:
    op.execute(_MIGRATE_UMANTIS_PROVIDER_IDENTITIES)


def downgrade() -> None:
    # Locale availability changes over time, so the previous observed suffix
    # cannot be reconstructed safely. Fail instead of moving Alembic's marker
    # backward while leaving the database in the post-migration identity state.
    raise RuntimeError("Umantis provider-ID identity migration cannot be downgraded safely")
