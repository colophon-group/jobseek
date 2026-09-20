"""Collapse volatile Jobs.cz tracking URLs onto stable job identities.

Two DOM boards published the same ``/rpd/<id>/`` job with a fresh ``searchId``
query on each monitor cycle.  Since URL-only monitors historically used the
whole URL as identity, nine live FedEx jobs expanded into thousands of active
rows.  Keep the best existing row per stable path, rewrite that survivor to
the query-free URL, and tombstone the tracking variants for normal CDC export.

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-20
"""

from __future__ import annotations

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_MAX_CANDIDATES = 10_000

_COLLAPSE_JOBS_CZ_TRACKING_URLS = f"""
DO $migration$
DECLARE
    candidate_count bigint;
BEGIN
    SELECT count(*) INTO candidate_count
    FROM job_posting AS posting
    JOIN job_board AS board ON board.id = posting.board_id
    WHERE board.board_slug IN (
        'fedex-czechia-local',
        'ferring-pharmaceuticals-careers-cz'
    )
      AND posting.source_url ~ '^https://www[.]jobs[.]cz/rpd/[0-9]+/[?][^#]+$';

    IF candidate_count > {_MAX_CANDIDATES} THEN
        RAISE EXCEPTION
            'Jobs.cz tracking URL collapse exceeded safety bound: %',
            candidate_count;
    END IF;

    CREATE TEMP TABLE _jobs_cz_tracking_map ON COMMIT DROP AS
    WITH ranked AS (
        SELECT
            posting.id AS candidate_id,
            posting.board_id,
            regexp_replace(posting.source_url, '[?].*$', '') AS canonical_url,
            first_value(posting.id) OVER (
                PARTITION BY
                    posting.board_id,
                    regexp_replace(posting.source_url, '[?].*$', '')
                ORDER BY
                    (cardinality(posting.titles) > 0) DESC,
                    (posting.description_r2_hash IS NOT NULL) DESC,
                    posting.is_active DESC,
                    posting.last_seen_at DESC NULLS LAST,
                    posting.first_seen_at,
                    posting.id
            ) AS ranked_survivor_id
        FROM job_posting AS posting
        JOIN job_board AS board ON board.id = posting.board_id
        WHERE board.board_slug IN (
            'fedex-czechia-local',
            'ferring-pharmaceuticals-careers-cz'
        )
          AND posting.source_url ~ '^https://www[.]jobs[.]cz/rpd/[0-9]+/[?][^#]+$'
    )
    SELECT
        ranked.candidate_id,
        ranked.canonical_url,
        coalesce(canonical.id, ranked.ranked_survivor_id) AS survivor_id
    FROM ranked
    LEFT JOIN job_posting AS canonical
      ON canonical.source_url = ranked.canonical_url;

    -- Updating source_url also advances legacy URL-as-identity rows through
    -- the source-identity trigger installed by migration 0023.
    UPDATE job_posting AS survivor
    SET source_url = mapping.canonical_url,
        updated_at = clock_timestamp()
    FROM (
        SELECT DISTINCT survivor_id, canonical_url
        FROM _jobs_cz_tracking_map
    ) AS mapping
    WHERE survivor.id = mapping.survivor_id
      AND survivor.source_url <> mapping.canonical_url;

    UPDATE job_posting AS duplicate
    SET is_active = false,
        next_scrape_at = NULL,
        leased_until = NULL,
        updated_at = clock_timestamp()
    FROM _jobs_cz_tracking_map AS mapping
    WHERE duplicate.id = mapping.candidate_id
      AND duplicate.id <> mapping.survivor_id
      AND (
          duplicate.is_active
          OR duplicate.next_scrape_at IS NOT NULL
          OR duplicate.leased_until IS NOT NULL
      );

    IF EXISTS (
        SELECT 1
        FROM job_posting AS posting
        JOIN job_board AS board ON board.id = posting.board_id
        WHERE board.board_slug IN (
            'fedex-czechia-local',
            'ferring-pharmaceuticals-careers-cz'
        )
          AND posting.is_active
        GROUP BY
            posting.board_id,
            regexp_replace(posting.source_url, '[?].*$', '')
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Jobs.cz tracking URL collapse left active duplicates';
    END IF;
END
$migration$;
"""


def upgrade() -> None:
    op.execute(_COLLAPSE_JOBS_CZ_TRACKING_URLS)


def downgrade() -> None:
    # Volatile tracking aliases cannot be reconstructed deterministically.
    pass
