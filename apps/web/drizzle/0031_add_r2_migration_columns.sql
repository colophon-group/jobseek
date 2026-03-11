-- Phase 2: Add new columns for R2 migration (all additive, metadata-only on PG11+)
ALTER TABLE job_posting ADD COLUMN is_active boolean NOT NULL DEFAULT true;
ALTER TABLE job_posting ADD COLUMN locales text[] NOT NULL DEFAULT '{}';
ALTER TABLE job_posting ADD COLUMN titles text[] NOT NULL DEFAULT '{}';
ALTER TABLE job_posting ADD COLUMN location_ids integer[];
ALTER TABLE job_posting ADD COLUMN location_types text[];
ALTER TABLE job_posting ADD COLUMN description_r2_hash bigint;
ALTER TABLE job_board ADD COLUMN scrape_interval_hours integer NOT NULL DEFAULT 24;
