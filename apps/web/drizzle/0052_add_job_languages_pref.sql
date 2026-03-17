ALTER TABLE "user_preferences"
ADD COLUMN "job_languages" text[] NOT NULL DEFAULT '{}';
