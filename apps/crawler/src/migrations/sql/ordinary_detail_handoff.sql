-- A completed native detail receipt must retain its next independently
-- scheduled parser step until atomic Redis settlement publishes it.
ALTER TABLE public.ordinary_worker_write_fence
    ADD COLUMN next_detail_step smallint,
    ADD COLUMN next_detail_at timestamptz,
    ADD COLUMN next_detail_description_hash bigint,
    ADD CONSTRAINT ordinary_detail_handoff_valid CHECK (
        (next_detail_step IS NULL AND next_detail_at IS NULL AND next_detail_description_hash IS NULL)
        OR (task_kind = 'scrape' AND next_detail_step BETWEEN 1 AND 7
            AND next_detail_step IS NOT NULL AND next_detail_at IS NOT NULL
            AND isfinite(next_detail_at))
    );
