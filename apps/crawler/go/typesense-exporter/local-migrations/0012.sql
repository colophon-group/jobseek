-- Running upgrade 0011 -> 0012

CREATE OR REPLACE FUNCTION jobseek_job_posting_cdc_writer_lock()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock_shared(18933879273374539);
            RETURN NULL;
        END;
        $$;

CREATE OR REPLACE FUNCTION jobseek_job_posting_cdc_stamp()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                NEW.updated_at := clock_timestamp();
            ELSIF NEW.updated_at IS DISTINCT FROM OLD.updated_at
                  OR ROW(NEW.is_active, NEW.titles, NEW.locales, NEW.location_ids, NEW.location_types, NEW.employment_type, NEW.salary_min, NEW.salary_max, NEW.salary_currency, NEW.salary_period, NEW.salary_eur, NEW.experience_min, NEW.experience_max, NEW.occupation_id, NEW.seniority_id, NEW.technology_ids, NEW.description_r2_hash)
                     IS DISTINCT FROM ROW(OLD.is_active, OLD.titles, OLD.locales, OLD.location_ids, OLD.location_types, OLD.employment_type, OLD.salary_min, OLD.salary_max, OLD.salary_currency, OLD.salary_period, OLD.salary_eur, OLD.experience_min, OLD.experience_max, OLD.occupation_id, OLD.seniority_id, OLD.technology_ids, OLD.description_r2_hash)
            THEN
                NEW.updated_at := clock_timestamp();
            END IF;
            RETURN NEW;
        END;
        $$;

DROP TRIGGER IF EXISTS job_posting_cdc_lock_insert ON job_posting;

DROP TRIGGER IF EXISTS job_posting_cdc_lock_update ON job_posting;

DROP TRIGGER IF EXISTS job_posting_cdc_stamp_insert ON job_posting;

DROP TRIGGER IF EXISTS job_posting_cdc_stamp_update ON job_posting;

CREATE TRIGGER job_posting_cdc_lock_insert
        BEFORE INSERT ON job_posting
        FOR EACH STATEMENT
        EXECUTE FUNCTION jobseek_job_posting_cdc_writer_lock();

CREATE TRIGGER job_posting_cdc_lock_update
        BEFORE UPDATE OF is_active, titles, locales, location_ids, location_types, employment_type, salary_min, salary_max, salary_currency, salary_period, salary_eur, experience_min, experience_max, occupation_id, seniority_id, technology_ids, description_r2_hash, updated_at ON job_posting
        FOR EACH STATEMENT
        EXECUTE FUNCTION jobseek_job_posting_cdc_writer_lock();

CREATE TRIGGER job_posting_cdc_stamp_insert
        BEFORE INSERT ON job_posting
        FOR EACH ROW
        EXECUTE FUNCTION jobseek_job_posting_cdc_stamp();

CREATE TRIGGER job_posting_cdc_stamp_update
        BEFORE UPDATE OF is_active, titles, locales, location_ids, location_types, employment_type, salary_min, salary_max, salary_currency, salary_period, salary_eur, experience_min, experience_max, occupation_id, seniority_id, technology_ids, description_r2_hash, updated_at ON job_posting
        FOR EACH ROW
        EXECUTE FUNCTION jobseek_job_posting_cdc_stamp();

UPDATE alembic_version SET version_num='0012' WHERE alembic_version.version_num = '0011';
