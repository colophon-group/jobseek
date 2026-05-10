-- Relax chk_employment_type to allow all 7 canonical employment-type values (#3006).
--
-- Backstory: 0033_normalize_employment_type added the constraint with 5
-- allowed values (full_time, part_time, contract, internship, full_or_part).
-- Since then, the crawler-side normalizer in
-- apps/crawler/src/core/enum_normalize.py::_EMPLOYMENT_TYPE_MAP has gained
-- two additional canonical buckets — `temporary` and `volunteer`.
-- Local Postgres + Typesense accept all 7; only Supabase's check constraint
-- drifted, blocking the CDC exporter.
--
-- Empirical (2026-05-10): exporter cursor stuck at
-- 2026-05-10T00:02:13.480402+00 because the next row past the cursor
-- (Tesco posting 68acdabe-5daa-416d-87c9-0b0a5de1ff85, employment_type=
-- 'temporary') fails chk_employment_type and rolls back the bulk batch.
-- Result: 12k-row export lag visible on Grafana.
--
-- Fix: drop + re-add chk_employment_type with all 7 canonical values from
-- _EMPLOYMENT_TYPE_MAP. NOT VALID + VALIDATE pattern matches 0033 (so any
-- pre-existing rows that somehow snuck in are validated post-add).
--
-- Idempotent on purpose: DROP IF EXISTS makes this safe to re-run.

ALTER TABLE job_posting DROP CONSTRAINT IF EXISTS chk_employment_type;

ALTER TABLE job_posting ADD CONSTRAINT chk_employment_type
  CHECK (employment_type IS NULL OR employment_type IN (
    'full_time',
    'part_time',
    'contract',
    'internship',
    'temporary',
    'volunteer',
    'full_or_part'
  ))
  NOT VALID;

ALTER TABLE job_posting VALIDATE CONSTRAINT chk_employment_type;
