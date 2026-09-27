-- Durable conservative reservations: failed/ambiguous attempts keep their quota.
CREATE TABLE public.notification_quota (
  period text PRIMARY KEY,
  used integer NOT NULL DEFAULT 0 CHECK (used >= 0)
);
ALTER TABLE public.notification_quota ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.notification_quota FROM anon, authenticated;

--> statement-breakpoint
-- A definitively rejected attempt may later have no matches or be opted out.
-- Preserve its attempt history while advancing an empty, unsent window.
ALTER TABLE public.notification_delivery DROP CONSTRAINT notification_delivery_skipped_check;
ALTER TABLE public.notification_delivery ADD CONSTRAINT notification_delivery_skipped_check CHECK (
  status <> 'skipped' OR (
    match_count IS NOT NULL AND match_count = 0 AND provider_message_id IS NULL
    AND ((provider_attempt_count = 0 AND last_provider_attempt_at IS NULL)
      OR (provider_attempt_count > 0 AND last_provider_attempt_at IS NOT NULL))
  )
);

--> statement-breakpoint
-- Existing opt-ins keep their broad result scope until the owner chooses otherwise.
ALTER TABLE public.watchlist ADD COLUMN alerts_narrowed_only boolean NOT NULL DEFAULT false;
