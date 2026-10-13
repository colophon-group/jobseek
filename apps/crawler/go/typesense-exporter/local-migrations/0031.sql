-- Running upgrade 0030 -> 0031

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

    IF candidate_count > 500 THEN
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
$migration$;;

UPDATE alembic_version SET version_num='0031' WHERE alembic_version.version_num = '0030';
