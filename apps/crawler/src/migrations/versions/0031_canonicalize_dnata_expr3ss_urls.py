"""Canonicalize Dnata Expr3ss detail URLs onto the working stable alias.

Release 0.13.806 briefly stored the two Australian Dnata boards without the
stable ``s=250`` query parameter.  Expr3ss serves those URLs as soft-success
pages without job JSON-LD.  Rewrite the narrowly scoped URL-as-identity rows
before the corrected monitor runs so it preserves posting identity instead of
creating duplicates.

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-21
"""

from __future__ import annotations

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_MAX_CANDIDATES = 500

_CANONICALIZE_DNATA_EXPR3SS_URLS = f"""
DO $migration$
DECLARE
    candidate_count bigint;
BEGIN
    SELECT count(*) INTO candidate_count
    FROM job_posting AS posting
    JOIN job_board AS board ON board.id = posting.board_id
    WHERE (
        board.board_slug = 'emirates-group-dnata-au'
        AND posting.source_url ~
            '^https://dnata[.]expr3ss[.]com/jobDetailsModern[?]selectJob=[0-9]+&modern=1$'
    ) OR (
        board.board_slug = 'emirates-group-dnata-catering-au'
        AND posting.source_url ~
            '^https://dnatacatering[.]expr3ss[.]com/jobDetailsModern[?]selectJob=[0-9]+&modern=1$'
    );

    IF candidate_count > {_MAX_CANDIDATES} THEN
        RAISE EXCEPTION
            'Dnata Expr3ss URL repair exceeded safety bound: %',
            candidate_count;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM job_posting AS candidate
        JOIN job_board AS board ON board.id = candidate.board_id
        JOIN job_posting AS canonical
          ON canonical.source_url = regexp_replace(
              candidate.source_url,
              '&modern=1$',
              '&s=250&modern=1'
          )
         AND canonical.id <> candidate.id
        WHERE (
            board.board_slug = 'emirates-group-dnata-au'
            AND candidate.source_url ~
                '^https://dnata[.]expr3ss[.]com/jobDetailsModern[?]selectJob=[0-9]+&modern=1$'
        ) OR (
            board.board_slug = 'emirates-group-dnata-catering-au'
            AND candidate.source_url ~
                '^https://dnatacatering[.]expr3ss[.]com/jobDetailsModern[?]selectJob=[0-9]+&modern=1$'
        )
    ) THEN
        RAISE EXCEPTION 'Dnata Expr3ss URL repair found a canonical URL collision';
    END IF;

    -- The migration-0023 trigger advances legacy URL-as-identity rows when
    -- source_url changes, while preserving any independently durable identity.
    UPDATE job_posting AS posting
    SET source_url = regexp_replace(
            posting.source_url,
            '&modern=1$',
            '&s=250&modern=1'
        ),
        updated_at = clock_timestamp()
    FROM job_board AS board
    WHERE board.id = posting.board_id
      AND (
          (
              board.board_slug = 'emirates-group-dnata-au'
              AND posting.source_url ~
                  '^https://dnata[.]expr3ss[.]com/jobDetailsModern[?]selectJob=[0-9]+&modern=1$'
          ) OR (
              board.board_slug = 'emirates-group-dnata-catering-au'
              AND posting.source_url ~
                  '^https://dnatacatering[.]expr3ss[.]com/jobDetailsModern[?]selectJob=[0-9]+&modern=1$'
          )
      );

    IF EXISTS (
        SELECT 1
        FROM job_posting AS posting
        JOIN job_board AS board ON board.id = posting.board_id
        WHERE (
            board.board_slug = 'emirates-group-dnata-au'
            AND posting.source_url ~
                '^https://dnata[.]expr3ss[.]com/jobDetailsModern[?]selectJob=[0-9]+&modern=1$'
        ) OR (
            board.board_slug = 'emirates-group-dnata-catering-au'
            AND posting.source_url ~
                '^https://dnatacatering[.]expr3ss[.]com/jobDetailsModern[?]selectJob=[0-9]+&modern=1$'
        )
    ) THEN
        RAISE EXCEPTION 'Dnata Expr3ss URL repair left noncanonical rows';
    END IF;
END
$migration$;
"""


def upgrade() -> None:
    op.execute(_CANONICALIZE_DNATA_EXPR3SS_URLS)


def downgrade() -> None:
    # The discarded soft-success alias is not a valid detail destination.
    pass
