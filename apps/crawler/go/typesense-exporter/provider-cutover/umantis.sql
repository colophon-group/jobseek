
DO $jobseek$
BEGIN
    -- A brand-new database has no CSV-synced boards when Alembic runs. Allow
    -- exactly that empty state; once any historical contract board exists,
    -- require the complete seven-board production registry.
    IF (
        SELECT count(board.id)
        FROM (VALUES
        ('bobst-global', 'bobst', 'https://jobs.bobst.com/Jobs/All', 'https://recruitingapp-2882.umantis.com', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('bucherer-careers-umantis', 'bucherer', 'https://recruitingapp-2840.umantis.com/Jobs/All', 'https://recruitingapp-2840.umantis.com', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('canton-neuchatel-careers', 'canton-neuchatel', 'https://recruitingapp-2702.umantis.com/Jobs/All', 'https://recruitingapp-2702.umantis.com', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('fhgr-careers', 'fhgr', 'https://recruitingapp-2865.umantis.com/Jobs/All', 'https://recruitingapp-2865.umantis.com', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('j-safra-sarasin-careers', 'j-safra-sarasin', 'https://jsafrasarasin.umantis.com/Jobs/All', 'https://jsafrasarasin.umantis.com', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('lindt-spruengli-careers', 'lindt-spruengli', 'https://www.lindt-spruengli.com/careers/vacancies', 'https://recruitingapp-1619.umantis.com', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('ruag-main', 'ruag', 'https://recruiting.ruag.ch/Jobs/All', 'https://recruitingapp-2514.umantis.com', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description$')
        ) AS contract (
            board_slug, company_slug, board_url, source_base,
            legacy_pattern, canonical_pattern
        )
        LEFT JOIN job_board AS board
          ON board.board_slug = contract.board_slug
    ) NOT IN (0, 7)
    OR EXISTS (
        SELECT 1
        FROM (VALUES
        ('bobst-global', 'bobst', 'https://jobs.bobst.com/Jobs/All', 'https://recruitingapp-2882.umantis.com', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('bucherer-careers-umantis', 'bucherer', 'https://recruitingapp-2840.umantis.com/Jobs/All', 'https://recruitingapp-2840.umantis.com', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('canton-neuchatel-careers', 'canton-neuchatel', 'https://recruitingapp-2702.umantis.com/Jobs/All', 'https://recruitingapp-2702.umantis.com', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('fhgr-careers', 'fhgr', 'https://recruitingapp-2865.umantis.com/Jobs/All', 'https://recruitingapp-2865.umantis.com', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('j-safra-sarasin-careers', 'j-safra-sarasin', 'https://jsafrasarasin.umantis.com/Jobs/All', 'https://jsafrasarasin.umantis.com', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('lindt-spruengli-careers', 'lindt-spruengli', 'https://www.lindt-spruengli.com/careers/vacancies', 'https://recruitingapp-1619.umantis.com', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('ruag-main', 'ruag', 'https://recruiting.ruag.ch/Jobs/All', 'https://recruitingapp-2514.umantis.com', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description$')
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
        ('bobst-global', 'bobst', 'https://jobs.bobst.com/Jobs/All', 'https://recruitingapp-2882.umantis.com', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('bucherer-careers-umantis', 'bucherer', 'https://recruitingapp-2840.umantis.com/Jobs/All', 'https://recruitingapp-2840.umantis.com', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('canton-neuchatel-careers', 'canton-neuchatel', 'https://recruitingapp-2702.umantis.com/Jobs/All', 'https://recruitingapp-2702.umantis.com', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('fhgr-careers', 'fhgr', 'https://recruitingapp-2865.umantis.com/Jobs/All', 'https://recruitingapp-2865.umantis.com', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('j-safra-sarasin-careers', 'j-safra-sarasin', 'https://jsafrasarasin.umantis.com/Jobs/All', 'https://jsafrasarasin.umantis.com', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('lindt-spruengli-careers', 'lindt-spruengli', 'https://www.lindt-spruengli.com/careers/vacancies', 'https://recruitingapp-1619.umantis.com', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('ruag-main', 'ruag', 'https://recruiting.ruag.ch/Jobs/All', 'https://recruitingapp-2514.umantis.com', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description$')
        ) AS contract (
            board_slug, company_slug, board_url, source_base,
            legacy_pattern, canonical_pattern
        )
        JOIN job_board AS board
          ON board.board_slug = contract.board_slug
        JOIN job_posting AS posting
          ON posting.board_id = board.id
         AND posting.source_url ~ contract.canonical_pattern
        LEFT JOIN crawler_identity_migration_receipt AS ledger
          ON ledger.migration_id = 'umantis-stable-description-v1'
         AND ledger.board_id = board.id
        WHERE board.metadata -> '_identity_migration_receipt' IS NULL
           OR ledger.board_id IS NULL
    ) THEN
        RAISE EXCEPTION 'Umantis identity migration found canonical URLs without an exact receipt';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (VALUES
        ('bobst-global', 'bobst', 'https://jobs.bobst.com/Jobs/All', 'https://recruitingapp-2882.umantis.com', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('bucherer-careers-umantis', 'bucherer', 'https://recruitingapp-2840.umantis.com/Jobs/All', 'https://recruitingapp-2840.umantis.com', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('canton-neuchatel-careers', 'canton-neuchatel', 'https://recruitingapp-2702.umantis.com/Jobs/All', 'https://recruitingapp-2702.umantis.com', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('fhgr-careers', 'fhgr', 'https://recruitingapp-2865.umantis.com/Jobs/All', 'https://recruitingapp-2865.umantis.com', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('j-safra-sarasin-careers', 'j-safra-sarasin', 'https://jsafrasarasin.umantis.com/Jobs/All', 'https://jsafrasarasin.umantis.com', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('lindt-spruengli-careers', 'lindt-spruengli', 'https://www.lindt-spruengli.com/careers/vacancies', 'https://recruitingapp-1619.umantis.com', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('ruag-main', 'ruag', 'https://recruiting.ruag.ch/Jobs/All', 'https://recruitingapp-2514.umantis.com', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description$')
        ) AS contract (
            board_slug, company_slug, board_url, source_base,
            legacy_pattern, canonical_pattern
        )
        JOIN job_board AS board
          ON board.board_slug = contract.board_slug
        LEFT JOIN crawler_identity_migration_receipt AS ledger
          ON ledger.migration_id = 'umantis-stable-description-v1'
         AND ledger.board_id = board.id
        CROSS JOIN LATERAL (
            SELECT board.metadata -> '_identity_migration_receipt' AS value
        ) AS receipt
        CROSS JOIN LATERAL (
            SELECT count(*) AS total_count
            FROM job_posting
            WHERE board_id = board.id
        ) AS current_state
        WHERE (receipt.value IS NULL) IS DISTINCT FROM (ledger.board_id IS NULL)
           OR (
              receipt.value IS NOT NULL
              AND (
                  jsonb_typeof(receipt.value) IS DISTINCT FROM 'object'
                  OR jsonb_typeof(receipt.value -> 'id') IS DISTINCT FROM 'string'
                  OR receipt.value ->> 'id' IS DISTINCT FROM ledger.migration_id
                  OR jsonb_typeof(receipt.value -> 'version') IS DISTINCT FROM 'number'
                  OR receipt.value ->> 'version' IS DISTINCT FROM ledger.version::text
                  OR receipt.value -> 'completed_at'
                     IS DISTINCT FROM to_jsonb(ledger.completed_at)
                  OR jsonb_typeof(receipt.value -> 'migrated_count')
                     IS DISTINCT FROM 'number'
                  OR receipt.value -> 'migrated_count'
                     IS DISTINCT FROM to_jsonb(ledger.migrated_count)
                  OR jsonb_typeof(receipt.value -> 'total_count')
                     IS DISTINCT FROM 'number'
                  OR receipt.value -> 'total_count'
                     IS DISTINCT FROM to_jsonb(ledger.total_count)
                  OR ledger.version IS DISTINCT FROM 1
                  OR ledger.migrated_count IS DISTINCT FROM ledger.total_count
                  OR ledger.total_count > current_state.total_count
                  OR ARRAY(
                      SELECT key
                      FROM jsonb_object_keys(receipt.value) AS key
                      ORDER BY key
                  ) IS DISTINCT FROM ARRAY[
                      'completed_at', 'id', 'migrated_count', 'total_count', 'version'
                  ]
              )
           )
    ) THEN
        RAISE EXCEPTION 'Umantis identity migration receipt mismatch';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (VALUES
        ('bobst-global', 'bobst', 'https://jobs.bobst.com/Jobs/All', 'https://recruitingapp-2882.umantis.com', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('bucherer-careers-umantis', 'bucherer', 'https://recruitingapp-2840.umantis.com/Jobs/All', 'https://recruitingapp-2840.umantis.com', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('canton-neuchatel-careers', 'canton-neuchatel', 'https://recruitingapp-2702.umantis.com/Jobs/All', 'https://recruitingapp-2702.umantis.com', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('fhgr-careers', 'fhgr', 'https://recruitingapp-2865.umantis.com/Jobs/All', 'https://recruitingapp-2865.umantis.com', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('j-safra-sarasin-careers', 'j-safra-sarasin', 'https://jsafrasarasin.umantis.com/Jobs/All', 'https://jsafrasarasin.umantis.com', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('lindt-spruengli-careers', 'lindt-spruengli', 'https://www.lindt-spruengli.com/careers/vacancies', 'https://recruitingapp-1619.umantis.com', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('ruag-main', 'ruag', 'https://recruiting.ruag.ch/Jobs/All', 'https://recruitingapp-2514.umantis.com', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description$')
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
        ('bobst-global', 'bobst', 'https://jobs.bobst.com/Jobs/All', 'https://recruitingapp-2882.umantis.com', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('bucherer-careers-umantis', 'bucherer', 'https://recruitingapp-2840.umantis.com/Jobs/All', 'https://recruitingapp-2840.umantis.com', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('canton-neuchatel-careers', 'canton-neuchatel', 'https://recruitingapp-2702.umantis.com/Jobs/All', 'https://recruitingapp-2702.umantis.com', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('fhgr-careers', 'fhgr', 'https://recruitingapp-2865.umantis.com/Jobs/All', 'https://recruitingapp-2865.umantis.com', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('j-safra-sarasin-careers', 'j-safra-sarasin', 'https://jsafrasarasin.umantis.com/Jobs/All', 'https://jsafrasarasin.umantis.com', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('lindt-spruengli-careers', 'lindt-spruengli', 'https://www.lindt-spruengli.com/careers/vacancies', 'https://recruitingapp-1619.umantis.com', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('ruag-main', 'ruag', 'https://recruiting.ruag.ch/Jobs/All', 'https://recruitingapp-2514.umantis.com', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description$')
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
        ('bobst-global', 'bobst', 'https://jobs.bobst.com/Jobs/All', 'https://recruitingapp-2882.umantis.com', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('bucherer-careers-umantis', 'bucherer', 'https://recruitingapp-2840.umantis.com/Jobs/All', 'https://recruitingapp-2840.umantis.com', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('canton-neuchatel-careers', 'canton-neuchatel', 'https://recruitingapp-2702.umantis.com/Jobs/All', 'https://recruitingapp-2702.umantis.com', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('fhgr-careers', 'fhgr', 'https://recruitingapp-2865.umantis.com/Jobs/All', 'https://recruitingapp-2865.umantis.com', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('j-safra-sarasin-careers', 'j-safra-sarasin', 'https://jsafrasarasin.umantis.com/Jobs/All', 'https://jsafrasarasin.umantis.com', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('lindt-spruengli-careers', 'lindt-spruengli', 'https://www.lindt-spruengli.com/careers/vacancies', 'https://recruitingapp-1619.umantis.com', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('ruag-main', 'ruag', 'https://recruiting.ruag.ch/Jobs/All', 'https://recruitingapp-2514.umantis.com', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description$')
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
        ('bobst-global', 'bobst', 'https://jobs.bobst.com/Jobs/All', 'https://recruitingapp-2882.umantis.com', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('bucherer-careers-umantis', 'bucherer', 'https://recruitingapp-2840.umantis.com/Jobs/All', 'https://recruitingapp-2840.umantis.com', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('canton-neuchatel-careers', 'canton-neuchatel', 'https://recruitingapp-2702.umantis.com/Jobs/All', 'https://recruitingapp-2702.umantis.com', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('fhgr-careers', 'fhgr', 'https://recruitingapp-2865.umantis.com/Jobs/All', 'https://recruitingapp-2865.umantis.com', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('j-safra-sarasin-careers', 'j-safra-sarasin', 'https://jsafrasarasin.umantis.com/Jobs/All', 'https://jsafrasarasin.umantis.com', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('lindt-spruengli-careers', 'lindt-spruengli', 'https://www.lindt-spruengli.com/careers/vacancies', 'https://recruitingapp-1619.umantis.com', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('ruag-main', 'ruag', 'https://recruiting.ruag.ch/Jobs/All', 'https://recruitingapp-2514.umantis.com', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description$')
        ) AS contract (
            board_slug, company_slug, board_url, source_base,
            legacy_pattern, canonical_pattern
        )
        JOIN job_board AS board
          ON board.board_slug = contract.board_slug
         AND board.metadata ? '_identity_migration_receipt'
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
        ('bobst-global', 'bobst', 'https://jobs.bobst.com/Jobs/All', 'https://recruitingapp-2882.umantis.com', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('bucherer-careers-umantis', 'bucherer', 'https://recruitingapp-2840.umantis.com/Jobs/All', 'https://recruitingapp-2840.umantis.com', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('canton-neuchatel-careers', 'canton-neuchatel', 'https://recruitingapp-2702.umantis.com/Jobs/All', 'https://recruitingapp-2702.umantis.com', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('fhgr-careers', 'fhgr', 'https://recruitingapp-2865.umantis.com/Jobs/All', 'https://recruitingapp-2865.umantis.com', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('j-safra-sarasin-careers', 'j-safra-sarasin', 'https://jsafrasarasin.umantis.com/Jobs/All', 'https://jsafrasarasin.umantis.com', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('lindt-spruengli-careers', 'lindt-spruengli', 'https://www.lindt-spruengli.com/careers/vacancies', 'https://recruitingapp-1619.umantis.com', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('ruag-main', 'ruag', 'https://recruiting.ruag.ch/Jobs/All', 'https://recruitingapp-2514.umantis.com', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description$')
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
), ledger AS (
    INSERT INTO crawler_identity_migration_receipt (
        migration_id, board_id, version, completed_at,
        migrated_count, total_count
    )
    SELECT 'umantis-stable-description-v1',
           before_state.board_id,
           1,
           clock_timestamp(),
           before_state.legacy_count,
           before_state.total_count
    FROM before_state
    JOIN job_board AS board
      ON board.id = before_state.board_id
    LEFT JOIN migration_count
      ON migration_count.board_id = before_state.board_id
    WHERE NOT (COALESCE(board.metadata, '{}'::jsonb) ? '_identity_migration_receipt')
      AND before_state.legacy_count = before_state.total_count
      AND COALESCE(migration_count.migrated_count, 0) = before_state.legacy_count
      AND NOT EXISTS (
          SELECT 1
          FROM crawler_identity_migration_receipt AS existing
          WHERE existing.migration_id = 'umantis-stable-description-v1'
            AND existing.board_id = before_state.board_id
      )
    RETURNING board_id, migration_id, version, completed_at,
              migrated_count, total_count
), receipt AS (
    UPDATE job_board AS board
    SET metadata = COALESCE(board.metadata, '{}'::jsonb)
                   || jsonb_build_object(
                        '_identity_migration_receipt',
                        jsonb_build_object(
                            'id', ledger.migration_id,
                            'version', ledger.version,
                            'completed_at', ledger.completed_at,
                            'migrated_count', ledger.migrated_count,
                            'total_count', ledger.total_count
                        )
                    ),
        updated_at = clock_timestamp()
    FROM ledger
    WHERE board.id = ledger.board_id
      AND NOT (COALESCE(board.metadata, '{}'::jsonb) ? '_identity_migration_receipt')
    RETURNING board.id
)
SELECT count(*) FROM receipt;

DO $verify$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (VALUES
        ('bobst-global', 'bobst', 'https://jobs.bobst.com/Jobs/All', 'https://recruitingapp-2882.umantis.com', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2882\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('bucherer-careers-umantis', 'bucherer', 'https://recruitingapp-2840.umantis.com/Jobs/All', 'https://recruitingapp-2840.umantis.com', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2840\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('canton-neuchatel-careers', 'canton-neuchatel', 'https://recruitingapp-2702.umantis.com/Jobs/All', 'https://recruitingapp-2702.umantis.com', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2702\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('fhgr-careers', 'fhgr', 'https://recruitingapp-2865.umantis.com/Jobs/All', 'https://recruitingapp-2865.umantis.com', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2865\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('j-safra-sarasin-careers', 'j-safra-sarasin', 'https://jsafrasarasin.umantis.com/Jobs/All', 'https://jsafrasarasin.umantis.com', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://jsafrasarasin\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('lindt-spruengli-careers', 'lindt-spruengli', 'https://www.lindt-spruengli.com/careers/vacancies', 'https://recruitingapp-1619.umantis.com', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-1619\.umantis\.com/Vacancies/[1-9][0-9]*/Description$'),
        ('ruag-main', 'ruag', 'https://recruiting.ruag.ch/Jobs/All', 'https://recruitingapp-2514.umantis.com', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description/[1-9][0-9]*$', '^https://recruitingapp\-2514\.umantis\.com/Vacancies/[1-9][0-9]*/Description$')
        ) AS contract (
            board_slug, company_slug, board_url, source_base,
            legacy_pattern, canonical_pattern
        )
        JOIN job_board AS board
          ON board.board_slug = contract.board_slug
        LEFT JOIN crawler_identity_migration_receipt AS ledger
          ON ledger.migration_id = 'umantis-stable-description-v1'
         AND ledger.board_id = board.id
        WHERE NOT (COALESCE(board.metadata, '{}'::jsonb) ? '_identity_migration_receipt')
           OR ledger.board_id IS NULL
           OR EXISTS (
               SELECT 1
               FROM job_posting AS posting
               WHERE posting.board_id = board.id
                 AND posting.source_url ~ contract.legacy_pattern
           )
    ) THEN
        RAISE EXCEPTION 'Umantis identity migration did not commit exact receipts';
    END IF;
END
$verify$;
