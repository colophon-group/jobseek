ALTER TABLE job_posting
  ALTER COLUMN experience_min TYPE numeric(3,1) USING experience_min::numeric(3,1),
  ALTER COLUMN experience_max TYPE numeric(3,1) USING experience_max::numeric(3,1);
