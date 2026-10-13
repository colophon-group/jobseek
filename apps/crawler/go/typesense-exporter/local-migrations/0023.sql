-- Running upgrade 0022 -> 0023

LOCK TABLE job_posting IN ACCESS EXCLUSIVE MODE;

CREATE TABLE posting_identity_migration_receipt (
    revision text PRIMARY KEY,
    expected_rows bigint NOT NULL CHECK (expected_rows >= 0),
    backfilled_rows bigint NOT NULL CHECK (backfilled_rows >= 0),
    source_url_min text,
    source_url_max text,
    completed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (expected_rows = backfilled_rows)
);

DO $contract$
DECLARE
    posting_count bigint;
    distinct_url_count bigint;
BEGIN
    SELECT count(*), count(DISTINCT source_url)
    INTO posting_count, distinct_url_count
    FROM job_posting;

    IF posting_count > 5000000 THEN
        RAISE EXCEPTION
            'durable identity backfill exceeds bounded row cap: % > 5000000',
            posting_count;
    END IF;
    IF posting_count <> distinct_url_count OR EXISTS (
        SELECT 1
        FROM job_posting
        WHERE company_id IS NULL OR NULLIF(btrim(source_url), '') IS NULL
    ) THEN
        RAISE EXCEPTION
            'durable identity backfill cannot prove unique, owned source URLs';
    END IF;
END
$contract$;

ALTER TABLE job_posting ADD COLUMN source_identity text;

DO $backfill$
DECLARE
    batch_count integer;
    total_count bigint := 0;
BEGIN
    LOOP
        WITH batch AS MATERIALIZED (
            SELECT ctid
            FROM job_posting
            WHERE source_identity IS NULL
            ORDER BY id
            LIMIT 50000
        )
        UPDATE job_posting AS posting
        SET source_identity = posting.source_url
        FROM batch
        WHERE posting.ctid = batch.ctid;

        GET DIAGNOSTICS batch_count = ROW_COUNT;
        total_count := total_count + batch_count;
        EXIT WHEN batch_count = 0;
    END LOOP;

    INSERT INTO posting_identity_migration_receipt (
        revision,
        expected_rows,
        backfilled_rows,
        source_url_min,
        source_url_max
    )
    SELECT
        '0023',
        count(*),
        total_count,
        min(source_url),
        max(source_url)
    FROM job_posting;
END
$backfill$;

ALTER TABLE job_posting ALTER COLUMN source_identity SET NOT NULL;
ALTER TABLE job_posting
    ADD CONSTRAINT job_posting_source_identity_key UNIQUE (source_identity);

CREATE TABLE job_posting_source_alias (
    source_url text PRIMARY KEY,
    posting_id uuid NOT NULL REFERENCES job_posting(id) ON DELETE CASCADE,
    first_observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT job_posting_source_alias_nonblank
        CHECK (NULLIF(btrim(source_url), '') IS NOT NULL),
    CONSTRAINT job_posting_source_alias_posting_url_key
        UNIQUE (posting_id, source_url)
);

CREATE OR REPLACE FUNCTION jobseek_job_posting_default_source_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.source_identity IS NULL THEN
        NEW.source_identity := NEW.source_url;
    ELSIF TG_OP = 'UPDATE'
          AND NEW.source_identity IS NOT DISTINCT FROM OLD.source_identity
          AND OLD.source_identity IS NOT DISTINCT FROM OLD.source_url
    THEN
        -- Preserve URL-as-identity semantics for old runtimes and bounded
        -- canonicalization migrations. Explicit provider identities differ
        -- from OLD.source_url and are therefore never rewritten here.
        NEW.source_identity := NEW.source_url;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER job_posting_default_source_identity
BEFORE INSERT OR UPDATE OF source_url ON job_posting
FOR EACH ROW
EXECUTE FUNCTION jobseek_job_posting_default_source_identity();;

CREATE OR REPLACE FUNCTION jobseek_job_posting_cdc_stamp()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.updated_at := clock_timestamp();
    ELSIF NEW.updated_at IS DISTINCT FROM OLD.updated_at
          OR ROW(
              NEW.source_url, NEW.is_active, NEW.titles, NEW.locales,
              NEW.location_ids, NEW.location_types, NEW.employment_type,
              NEW.salary_min, NEW.salary_max, NEW.salary_currency,
              NEW.salary_period, NEW.salary_eur, NEW.experience_min,
              NEW.experience_max, NEW.occupation_id, NEW.seniority_id,
              NEW.technology_ids, NEW.description_r2_hash
          ) IS DISTINCT FROM ROW(
              OLD.source_url, OLD.is_active, OLD.titles, OLD.locales,
              OLD.location_ids, OLD.location_types, OLD.employment_type,
              OLD.salary_min, OLD.salary_max, OLD.salary_currency,
              OLD.salary_period, OLD.salary_eur, OLD.experience_min,
              OLD.experience_max, OLD.occupation_id, OLD.seniority_id,
              OLD.technology_ids, OLD.description_r2_hash
          )
    THEN
        NEW.updated_at := clock_timestamp();
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS job_posting_cdc_lock_update ON job_posting;
DROP TRIGGER IF EXISTS job_posting_cdc_stamp_update ON job_posting;
CREATE TRIGGER job_posting_cdc_lock_update
BEFORE UPDATE OF source_url, is_active, titles, locales, location_ids,
    location_types, employment_type, salary_min, salary_max, salary_currency,
    salary_period, salary_eur, experience_min, experience_max, occupation_id,
    seniority_id, technology_ids, description_r2_hash, updated_at
ON job_posting
FOR EACH STATEMENT
EXECUTE FUNCTION jobseek_job_posting_cdc_writer_lock();
CREATE TRIGGER job_posting_cdc_stamp_update
BEFORE UPDATE OF source_url, is_active, titles, locales, location_ids,
    location_types, employment_type, salary_min, salary_max, salary_currency,
    salary_period, salary_eur, experience_min, experience_max, occupation_id,
    seniority_id, technology_ids, description_r2_hash, updated_at
ON job_posting
FOR EACH ROW
EXECUTE FUNCTION jobseek_job_posting_cdc_stamp();;

UPDATE alembic_version SET version_num='0023' WHERE alembic_version.version_num = '0022';
