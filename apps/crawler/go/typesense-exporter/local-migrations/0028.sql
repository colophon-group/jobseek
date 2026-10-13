-- Running upgrade 0027 -> 0028

DO $migration$
DECLARE
    canonical_id uuid;
    duplicate_id uuid;
BEGIN
    SELECT id INTO canonical_id FROM company WHERE slug = 'starbucks';
    SELECT id INTO duplicate_id FROM company WHERE slug = 'starbucks-china';

    IF duplicate_id IS NULL THEN
        RETURN;
    END IF;
    IF canonical_id IS NULL THEN
        RAISE EXCEPTION
            'cannot merge starbucks-china: canonical starbucks company is missing';
    END IF;

    UPDATE job_board
    SET company_id = canonical_id,
        updated_at = now()
    WHERE company_id = duplicate_id;

    UPDATE job_posting
    SET company_id = canonical_id,
        updated_at = now()
    WHERE company_id = duplicate_id;

    IF to_regclass('public.murmur_accept_log') IS NOT NULL THEN
        EXECUTE
            'UPDATE murmur_accept_log SET company_id = $1 WHERE company_id = $2'
            USING canonical_id, duplicate_id;
    END IF;

    IF to_regclass('public.company_description') IS NOT NULL THEN
        EXECUTE
            'DELETE FROM company_description WHERE company_id = $1'
            USING duplicate_id;
    END IF;

    IF EXISTS (SELECT 1 FROM job_board WHERE company_id = duplicate_id)
       OR EXISTS (SELECT 1 FROM job_posting WHERE company_id = duplicate_id) THEN
        RAISE EXCEPTION
            'cannot delete starbucks-china: crawler references remain';
    END IF;

    DELETE FROM company WHERE id = duplicate_id;
    IF FOUND IS NOT TRUE THEN
        RAISE EXCEPTION 'starbucks-china disappeared during identity merge';
    END IF;
END
$migration$;;

UPDATE alembic_version SET version_num='0028' WHERE alembic_version.version_num = '0027';
