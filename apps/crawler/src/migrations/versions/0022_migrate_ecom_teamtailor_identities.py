"""Canonicalize ECOM Teamtailor postings to stable numeric identities.

The crawler deployment quiesces every PostgreSQL writer before Alembic.  This
revision therefore converts the exact ECOM board before the new RSS runtime can
recrawl it.  All locale/title aliases are grouped by their fetchable regional
Teamtailor host plus numeric job ID; the retired Europe hostname is explicitly
mapped to its current tenant. One existing row retains its UUID and the rest
are retired. A bounded receipt captures the original rows so the deploy
transaction can restore them before starting the previous image if rollout
fails.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-26
"""

from __future__ import annotations

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_MIGRATION_ID = "ecom-teamtailor-stable-id-v1"
_MIGRATION_VERSION = 1
_CONFIG_FINGERPRINT = "3bed2708932dbf6324197581425ecb0347f00f06290c1e45ab7145836a7ee67f"
_COMPANY_SLUG = "ecom-agroindustrial"
_BOARD_SLUG = "ecom-agroindustrial-global"
_OLD_BOARD_URL = "https://careers.ecomtrading.com/jobs"
_BOARD_URL = "https://ecomtradinggroup.teamtailor.com/jobs"
_CURRENT_HOST_PATTERN = (
    r"(careerslatam[.]ecomtrading[.]com|"
    r"careerswestafrica[.]ecomtrading[.]com|"
    r"careersasiapacific[.]ecomtrading[.]com|"
    r"careersbrazil[.]ecomtrading[.]com|"
    r"careersmexico[.]ecomtrading[.]com|"
    r"ecomeurope[.]teamtailor[.]com)"
)
_OLD_EUROPE_HOST_PATTERN = r"careerseurope[.]ecomtrading[.]com"
_ALL_SOURCE_HOST_PATTERN = _CURRENT_HOST_PATTERN[:-1] + "|" + _OLD_EUROPE_HOST_PATTERN + ")"
_LEGACY_PATTERN = (
    # Keep every group capturing. SQLAlchemy would parse ``:...`` inside a
    # non-capturing regex group as a bind parameter before PostgreSQL sees it.
    rf"^https://{_ALL_SOURCE_HOST_PATTERN}/((de|fr|it|en)/)?jobs/[0-9]+-[^/?#]+$|"
    rf"^https://{_ALL_SOURCE_HOST_PATTERN}/(de|fr|it|en)/jobs/[0-9]+$|"
    rf"^https://{_OLD_EUROPE_HOST_PATTERN}/jobs/[0-9]+$"
)
_CANONICAL_PATTERN = rf"^https://{_CURRENT_HOST_PATTERN}/jobs/[0-9]+$"
_MAX_ROWS = 100

_MIGRATE_ECOM_TEAMTAILOR_IDENTITIES = f"""
DO $jobseek$
DECLARE
    target_board_id uuid;
    target_board_count integer;
    existing_receipt jsonb;
    candidate_count integer;
    unknown_active_count integer;
    collision_count integer;
    legacy_group_count integer;
    retired_count integer;
    canonicalized_count integer;
    rollback_rows jsonb;
BEGIN
    SELECT count(*)
    INTO target_board_count
    FROM job_board AS board
    JOIN company ON company.id = board.company_id
    WHERE company.slug = '{_COMPANY_SLUG}'
      AND board.board_slug = '{_BOARD_SLUG}'
      AND board.board_url IN ('{_OLD_BOARD_URL}', '{_BOARD_URL}')
      AND board.crawler_type = 'rss'
      AND board.metadata ->> '_monitor_config_fingerprint' = '{_CONFIG_FINGERPRINT}';

    IF target_board_count = 0 THEN
        RETURN;
    ELSIF target_board_count <> 1 THEN
        RAISE EXCEPTION 'ECOM identity migration found ambiguous board ownership';
    END IF;
    SELECT board.id, board.metadata -> '_identity_migration_receipt'
    INTO target_board_id, existing_receipt
    FROM job_board AS board
    JOIN company ON company.id = board.company_id
    WHERE company.slug = '{_COMPANY_SLUG}'
      AND board.board_slug = '{_BOARD_SLUG}'
      AND board.board_url IN ('{_OLD_BOARD_URL}', '{_BOARD_URL}')
      AND board.crawler_type = 'rss'
      AND board.metadata ->> '_monitor_config_fingerprint' = '{_CONFIG_FINGERPRINT}'
    FOR UPDATE OF board;

    IF existing_receipt IS NOT NULL THEN
        IF existing_receipt ->> 'id' = '{_MIGRATION_ID}'
           AND (existing_receipt ->> 'version')::integer = {_MIGRATION_VERSION}
           AND existing_receipt ->> 'config_fingerprint' = '{_CONFIG_FINGERPRINT}'
           AND jsonb_typeof(existing_receipt -> 'rollback_rows') = 'array'
           AND jsonb_array_length(existing_receipt -> 'rollback_rows') <= {_MAX_ROWS}
        THEN
            RETURN;
        END IF;
        RAISE EXCEPTION 'ECOM identity migration found a mismatched receipt';
    END IF;

    SELECT count(*),
           COALESCE(
               jsonb_agg(
                   jsonb_build_object(
                       'id', posting.id,
                       'source_url', posting.source_url,
                       'is_active', posting.is_active,
                       'missing_count', posting.missing_count,
                       'next_scrape_at', posting.next_scrape_at
                   )
                   ORDER BY posting.id
               ),
               '[]'::jsonb
           )
    INTO candidate_count, rollback_rows
    FROM job_posting AS posting
    WHERE posting.board_id = target_board_id
      AND (posting.source_url ~ '{_LEGACY_PATTERN}'
           OR posting.source_url ~ '{_CANONICAL_PATTERN}');

    IF candidate_count > {_MAX_ROWS} THEN
        RAISE EXCEPTION 'ECOM identity migration exceeds the bounded row cap';
    END IF;

    SELECT count(*)
    INTO unknown_active_count
    FROM job_posting AS posting
    WHERE posting.board_id = target_board_id
      AND posting.is_active = true
      AND posting.source_url !~ '{_LEGACY_PATTERN}'
      AND posting.source_url !~ '{_CANONICAL_PATTERN}';
    IF unknown_active_count <> 0 THEN
        RAISE EXCEPTION 'ECOM identity migration found unknown active source identities';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM job_posting AS posting
        WHERE posting.board_id <> target_board_id
          AND posting.source_url ~ '{_CANONICAL_PATTERN}'
    ) THEN
        RAISE EXCEPTION 'ECOM identity migration found foreign canonical URL ownership';
    END IF;

    WITH candidate_sources AS MATERIALIZED (
        SELECT posting.id,
               posting.source_url,
               posting.source_url ~ '{_CANONICAL_PATTERN}' AS is_canonical,
               'https://' ||
               CASE
                   WHEN substring(posting.source_url FROM '^https://([^/]+)') =
                        'careerseurope.ecomtrading.com'
                   THEN 'ecomeurope.teamtailor.com'
                   ELSE substring(posting.source_url FROM '^https://([^/]+)')
               END || '/jobs/' ||
               substring(posting.source_url FROM '/jobs/([0-9]+)') AS canonical_url
        FROM job_posting AS posting
        WHERE posting.board_id = target_board_id
          AND (posting.source_url ~ '{_LEGACY_PATTERN}'
               OR posting.source_url ~ '{_CANONICAL_PATTERN}')
    )
    SELECT count(*)
    INTO collision_count
    FROM (
        SELECT canonical_url
        FROM candidate_sources
        GROUP BY canonical_url
        HAVING count(*) FILTER (WHERE is_canonical) > 0
           AND count(*) FILTER (WHERE NOT is_canonical) > 0
    ) AS collisions;
    IF collision_count <> 0 THEN
        RAISE EXCEPTION 'ECOM identity migration found canonical/legacy row collisions';
    END IF;

    WITH legacy_sources AS MATERIALIZED (
        SELECT 'https://' ||
               CASE
                   WHEN substring(posting.source_url FROM '^https://([^/]+)') =
                        'careerseurope.ecomtrading.com'
                   THEN 'ecomeurope.teamtailor.com'
                   ELSE substring(posting.source_url FROM '^https://([^/]+)')
               END || '/jobs/' ||
               substring(posting.source_url FROM '/jobs/([0-9]+)') AS canonical_url
        FROM job_posting AS posting
        WHERE posting.board_id = target_board_id
          AND posting.source_url ~ '{_LEGACY_PATTERN}'
    )
    SELECT count(DISTINCT canonical_url)
    INTO legacy_group_count
    FROM legacy_sources;

    WITH candidates AS MATERIALIZED (
        SELECT posting.id,
               'https://' ||
               CASE
                   WHEN substring(posting.source_url FROM '^https://([^/]+)') =
                        'careerseurope.ecomtrading.com'
                   THEN 'ecomeurope.teamtailor.com'
                   ELSE substring(posting.source_url FROM '^https://([^/]+)')
               END || '/jobs/' ||
               substring(posting.source_url FROM '/jobs/([0-9]+)') AS canonical_url,
               row_number() OVER (
                   PARTITION BY
                       CASE
                           WHEN substring(posting.source_url FROM '^https://([^/]+)') =
                                'careerseurope.ecomtrading.com'
                           THEN 'ecomeurope.teamtailor.com'
                           ELSE substring(posting.source_url FROM '^https://([^/]+)')
                       END,
                       substring(posting.source_url FROM '/jobs/([0-9]+)')
                   ORDER BY posting.is_active DESC,
                            posting.last_seen_at DESC NULLS LAST,
                            posting.id
               ) AS identity_rank
        FROM job_posting AS posting
        WHERE posting.board_id = target_board_id
          AND posting.source_url ~ '{_LEGACY_PATTERN}'
    ), retired AS (
        UPDATE job_posting AS posting
        SET is_active = false,
            next_scrape_at = NULL,
            updated_at = now()
        FROM candidates
        WHERE posting.id = candidates.id
          AND candidates.identity_rank > 1
        RETURNING posting.id
    )
    SELECT count(*) INTO retired_count FROM retired;

    WITH candidates AS MATERIALIZED (
        SELECT posting.id,
               'https://' ||
               CASE
                   WHEN substring(posting.source_url FROM '^https://([^/]+)') =
                        'careerseurope.ecomtrading.com'
                   THEN 'ecomeurope.teamtailor.com'
                   ELSE substring(posting.source_url FROM '^https://([^/]+)')
               END || '/jobs/' ||
               substring(posting.source_url FROM '/jobs/([0-9]+)') AS canonical_url,
               row_number() OVER (
                   PARTITION BY
                       CASE
                           WHEN substring(posting.source_url FROM '^https://([^/]+)') =
                                'careerseurope.ecomtrading.com'
                           THEN 'ecomeurope.teamtailor.com'
                           ELSE substring(posting.source_url FROM '^https://([^/]+)')
                       END,
                       substring(posting.source_url FROM '/jobs/([0-9]+)')
                   ORDER BY posting.is_active DESC,
                            posting.last_seen_at DESC NULLS LAST,
                            posting.id
               ) AS identity_rank
        FROM job_posting AS posting
        WHERE posting.board_id = target_board_id
          AND posting.source_url ~ '{_LEGACY_PATTERN}'
    ), canonicalized AS (
        UPDATE job_posting AS posting
        SET source_url = candidates.canonical_url,
            updated_at = now()
        FROM candidates
        WHERE posting.id = candidates.id
          AND candidates.identity_rank = 1
        RETURNING posting.id
    )
    SELECT count(*) INTO canonicalized_count FROM canonicalized;
    IF canonicalized_count <> legacy_group_count THEN
        RAISE EXCEPTION 'ECOM identity migration did not canonicalize every legacy group';
    END IF;

    UPDATE job_board
    SET metadata = COALESCE(metadata, '{{}}'::jsonb)
                   || jsonb_build_object(
                       '_identity_migration_receipt',
                       jsonb_build_object(
                           'id', '{_MIGRATION_ID}',
                           'version', {_MIGRATION_VERSION},
                           'config_fingerprint', '{_CONFIG_FINGERPRINT}',
                           'completed_at', clock_timestamp(),
                           'retired_count', retired_count,
                           'rollback_rows', rollback_rows
                       )
                   ),
        updated_at = now()
    WHERE id = target_board_id;

END
$jobseek$;
"""

_ROLLBACK_ECOM_TEAMTAILOR_IDENTITIES = f"""
DO $jobseek$
DECLARE
    target_board_id uuid;
    target_board_count integer;
    existing_receipt jsonb;
    recorded_count integer;
    restored_count integer;
BEGIN
    SELECT count(*)
    INTO target_board_count
    FROM job_board AS board
    JOIN company ON company.id = board.company_id
    WHERE company.slug = '{_COMPANY_SLUG}'
      AND board.board_slug = '{_BOARD_SLUG}'
      AND board.board_url IN ('{_OLD_BOARD_URL}', '{_BOARD_URL}')
      AND board.crawler_type = 'rss'
      AND board.metadata ->> '_monitor_config_fingerprint' = '{_CONFIG_FINGERPRINT}';

    IF target_board_count = 0 THEN
        RETURN;
    ELSIF target_board_count <> 1 THEN
        RAISE EXCEPTION 'ECOM identity rollback found ambiguous board ownership';
    END IF;
    SELECT board.id, board.metadata -> '_identity_migration_receipt'
    INTO target_board_id, existing_receipt
    FROM job_board AS board
    JOIN company ON company.id = board.company_id
    WHERE company.slug = '{_COMPANY_SLUG}'
      AND board.board_slug = '{_BOARD_SLUG}'
      AND board.board_url IN ('{_OLD_BOARD_URL}', '{_BOARD_URL}')
      AND board.crawler_type = 'rss'
      AND board.metadata ->> '_monitor_config_fingerprint' = '{_CONFIG_FINGERPRINT}'
    FOR UPDATE OF board;
    IF existing_receipt IS NULL THEN
        RETURN;
    END IF;
    IF existing_receipt ->> 'id' <> '{_MIGRATION_ID}'
       OR (existing_receipt ->> 'version')::integer <> {_MIGRATION_VERSION}
       OR existing_receipt ->> 'config_fingerprint' <> '{_CONFIG_FINGERPRINT}'
       OR jsonb_typeof(existing_receipt -> 'rollback_rows') <> 'array'
       OR jsonb_array_length(existing_receipt -> 'rollback_rows') > {_MAX_ROWS}
    THEN
        RAISE EXCEPTION 'ECOM identity rollback found a mismatched receipt';
    END IF;

    recorded_count := jsonb_array_length(existing_receipt -> 'rollback_rows');
    IF (
        SELECT count(*)
        FROM jsonb_to_recordset(existing_receipt -> 'rollback_rows')
          AS original(id uuid, source_url text, is_active boolean,
                      missing_count integer, next_scrape_at timestamptz)
        JOIN job_posting AS posting ON posting.id = original.id
        WHERE posting.board_id = target_board_id
          AND (posting.source_url ~ '{_LEGACY_PATTERN}'
               OR posting.source_url ~ '{_CANONICAL_PATTERN}')
          AND (original.source_url ~ '{_LEGACY_PATTERN}'
               OR original.source_url ~ '{_CANONICAL_PATTERN}')
    ) <> recorded_count THEN
        RAISE EXCEPTION 'ECOM identity rollback row set no longer matches its receipt';
    END IF;
    IF (
        SELECT count(DISTINCT original.source_url)
        FROM jsonb_to_recordset(existing_receipt -> 'rollback_rows')
          AS original(id uuid, source_url text, is_active boolean,
                      missing_count integer, next_scrape_at timestamptz)
    ) <> recorded_count THEN
        RAISE EXCEPTION 'ECOM identity rollback receipt has duplicate source identities';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM jsonb_to_recordset(existing_receipt -> 'rollback_rows')
          AS original(id uuid, source_url text, is_active boolean,
                      missing_count integer, next_scrape_at timestamptz)
        JOIN job_posting AS foreign_posting
          ON foreign_posting.source_url = original.source_url
         AND foreign_posting.id <> original.id
    ) THEN
        RAISE EXCEPTION 'ECOM identity rollback found occupied legacy identities';
    END IF;

    WITH restored AS (
        UPDATE job_posting AS posting
        SET source_url = original.source_url,
            is_active = original.is_active,
            missing_count = original.missing_count,
            next_scrape_at = original.next_scrape_at,
            updated_at = now()
        FROM jsonb_to_recordset(existing_receipt -> 'rollback_rows')
          AS original(id uuid, source_url text, is_active boolean,
                      missing_count integer, next_scrape_at timestamptz)
        WHERE posting.id = original.id
          AND posting.board_id = target_board_id
        RETURNING posting.id
    )
    SELECT count(*) INTO restored_count FROM restored;
    IF restored_count <> recorded_count THEN
        RAISE EXCEPTION 'ECOM identity rollback did not restore every recorded row';
    END IF;

    UPDATE job_board
    SET metadata = metadata - '_identity_migration_receipt',
        updated_at = now()
    WHERE id = target_board_id;
END
$jobseek$;
"""


def upgrade() -> None:
    op.execute(_MIGRATE_ECOM_TEAMTAILOR_IDENTITIES)


def downgrade() -> None:
    op.execute(_ROLLBACK_ECOM_TEAMTAILOR_IDENTITIES)
