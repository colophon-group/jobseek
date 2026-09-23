ALTER TABLE "ai_filter_query_version"
  ADD COLUMN "candidate_languages" jsonb DEFAULT '[]'::jsonb NOT NULL;
