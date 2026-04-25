-- Add resume_customization_history table to track customizations
CREATE TABLE resume_customization_history (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
  queue_id UUID NOT NULL REFERENCES job_queue(id) ON DELETE CASCADE,
  posting_id UUID NOT NULL REFERENCES job_posting(id) ON DELETE CASCADE,
  
  -- Content tracking
  original_r2_key TEXT,
  customized_r2_key TEXT,
  inserted_keywords TEXT[] NOT NULL DEFAULT '{}',
  
  -- Metadata
  job_title TEXT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  
  CONSTRAINT fk_customization_user FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE,
  CONSTRAINT fk_customization_queue FOREIGN KEY (queue_id) REFERENCES job_queue(id) ON DELETE CASCADE,
  CONSTRAINT fk_customization_posting FOREIGN KEY (posting_id) REFERENCES job_posting(id) ON DELETE CASCADE
);

-- Index for fast lookups
CREATE INDEX idx_customization_user ON resume_customization_history(user_id);
CREATE INDEX idx_customization_queue ON resume_customization_history(queue_id);
CREATE INDEX idx_customization_created ON resume_customization_history(created_at DESC);
CREATE UNIQUE INDEX idx_customization_queue_unique ON resume_customization_history(queue_id) WHERE created_at = (SELECT MAX(created_at) FROM resume_customization_history);

-- Add customized_at column to user_resume table
ALTER TABLE user_resume ADD COLUMN IF NOT EXISTS customized_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE user_resume ADD COLUMN IF NOT EXISTS customization_count INTEGER DEFAULT 0;
