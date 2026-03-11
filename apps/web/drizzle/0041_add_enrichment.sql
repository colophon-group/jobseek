-- Add enrichment columns to job_posting
ALTER TABLE job_posting ADD COLUMN enrichment jsonb;
ALTER TABLE job_posting ADD COLUMN to_be_enriched boolean NOT NULL DEFAULT true;
ALTER TABLE job_posting ADD COLUMN enrich_version smallint NOT NULL DEFAULT 0;
ALTER TABLE job_posting ADD COLUMN last_enriched_at timestamptz;

-- Partial index for the enricher to find pending rows efficiently
CREATE INDEX idx_jp_to_be_enriched ON job_posting (to_be_enriched)
    WHERE is_active = true AND to_be_enriched = true;

-- Create enrich_batch table to track submitted LLM batches
CREATE TABLE enrich_batch (
    id text PRIMARY KEY,
    provider text NOT NULL,
    model text NOT NULL,
    status text NOT NULL DEFAULT 'submitted',
    item_count int NOT NULL,
    posting_ids uuid[] NOT NULL,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    input_tokens int,
    output_tokens int,
    estimated_cost_usd numeric(10,4),
    error text
);
