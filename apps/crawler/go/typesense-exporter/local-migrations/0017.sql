-- Running upgrade 0016 -> 0017

DO $migration$
BEGIN
    IF to_regclass('public.location') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM pg_constraint
           WHERE conrelid = to_regclass('public.location')
             AND conname = 'chk_location_slug_nonblank'
       ) THEN
        ALTER TABLE public.location
            ADD CONSTRAINT chk_location_slug_nonblank
            CHECK (slug IS NOT NULL AND btrim(slug) <> '') NOT VALID;
    END IF;
END
$migration$;;

UPDATE alembic_version SET version_num='0017' WHERE alembic_version.version_num = '0016';
