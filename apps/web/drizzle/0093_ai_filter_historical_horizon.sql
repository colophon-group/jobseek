ALTER TABLE public.ai_filter_decision
  DROP CONSTRAINT ai_filter_decision_retention_check;--> statement-breakpoint
ALTER TABLE public.ai_filter_decision
  ADD CONSTRAINT ai_filter_decision_retention_check
  CHECK (
    expires_at > decided_at
    AND expires_at <= decided_at + interval '30 days'
  );
