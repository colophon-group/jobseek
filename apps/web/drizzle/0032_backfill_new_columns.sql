-- Phase 2: Backfill new columns from existing data
UPDATE job_posting SET is_active = (status = 'active');
UPDATE job_posting SET
  locales = ARRAY[COALESCE(language, 'en')],
  titles = ARRAY[COALESCE(title, '')];
