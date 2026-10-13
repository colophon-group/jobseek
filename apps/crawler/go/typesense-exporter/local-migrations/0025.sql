-- Running upgrade 0024 -> 0025

DO $$
DECLARE
    intern_seniority_id integer;
BEGIN
    -- Taxonomy tables are populated by sync rather than Alembic on a brand-new
    -- database, so an empty installation has nothing to migrate yet.
    IF to_regclass('public.seniority') IS NULL THEN
        RETURN;
    END IF;

    SELECT id
    INTO intern_seniority_id
    FROM public.seniority
    WHERE slug = 'intern';

    IF intern_seniority_id IS NULL THEN
        RETURN;
    END IF;

    UPDATE public.job_posting
    SET seniority_id = intern_seniority_id,
        employment_type = NULL,
        updated_at = now()
    WHERE employment_type = 'internship';
END
$$;

UPDATE alembic_version SET version_num='0025' WHERE alembic_version.version_num = '0024';
