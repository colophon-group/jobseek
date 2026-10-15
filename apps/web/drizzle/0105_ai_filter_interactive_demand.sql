-- Additive: old deployments continue to consume the same durable queue.
ALTER TABLE public.ai_filter_work
  ADD COLUMN interactive_until timestamptz,
  ADD COLUMN accepted_target integer CHECK (accepted_target BETWEEN 1 AND 10000);
--> statement-breakpoint
ALTER TABLE public.ai_filter_worker
  ADD COLUMN interactive_streak integer NOT NULL DEFAULT 0 CHECK (interactive_streak BETWEEN 0 AND 3);
