-- Backend-only notification persistence for #8317 / #8366.
-- Existing and new alerts remain opt-in. Any legacy enabled row is floored at
-- migration time so this additive migration cannot expose a historical digest.

CREATE TYPE public.notification_cadence AS ENUM ('weekly');--> statement-breakpoint
CREATE TYPE public.notification_delivery_status AS ENUM (
  'pending',
  'sent',
  'skipped',
  'failed',
  'unknown',
  'quota_deferred'
);--> statement-breakpoint

ALTER TABLE public.user_preferences
  ADD COLUMN notification_cadence public.notification_cadence
    DEFAULT 'weekly' NOT NULL,
  ADD COLUMN notifications_paused boolean DEFAULT false NOT NULL,
  ADD COLUMN notifications_state_changed_at timestamp with time zone
    DEFAULT now() NOT NULL;--> statement-breakpoint

-- Keep the floor coupled to the boolean even if a future writer updates the
-- row outside the canonical service. Repeated writes preserve the old floor;
-- a real transition without an explicit timestamp receives database time.
CREATE FUNCTION public.jobseek_notifications_pause_state_changed_at()
RETURNS trigger
LANGUAGE plpgsql
AS $pause_state$
BEGIN
  IF NEW.notifications_paused IS NOT DISTINCT FROM OLD.notifications_paused THEN
    NEW.notifications_state_changed_at := OLD.notifications_state_changed_at;
  ELSIF NEW.notifications_state_changed_at
    IS NOT DISTINCT FROM OLD.notifications_state_changed_at
  THEN
    NEW.notifications_state_changed_at := statement_timestamp();
  END IF;
  RETURN NEW;
END
$pause_state$;--> statement-breakpoint

CREATE TRIGGER notifications_pause_state_changed_at_before_write
BEFORE UPDATE OF notifications_paused, notifications_state_changed_at
ON public.user_preferences
FOR EACH ROW
EXECUTE FUNCTION public.jobseek_notifications_pause_state_changed_at();--> statement-breakpoint

ALTER TABLE public.watchlist
  ADD COLUMN alerts_enabled_at timestamp with time zone;--> statement-breakpoint

UPDATE public.watchlist
SET alerts_enabled_at = statement_timestamp()
WHERE alerts_enabled = true
  AND alerts_enabled_at IS NULL;--> statement-breakpoint

-- Keep the migration compatible with the pre-migration web runtime during a
-- rolling deploy. Old code updates only alerts_enabled; the trigger supplies
-- or clears the timestamp while new code can provide an exact enable time.
CREATE FUNCTION public.jobseek_watchlist_alerts_enabled_at_compat()
RETURNS trigger
LANGUAGE plpgsql
AS $compat$
BEGIN
  IF NEW.alerts_enabled THEN
    IF NEW.alerts_enabled_at IS NULL
      OR (TG_OP = 'UPDATE' AND OLD.alerts_enabled = false)
    THEN
      NEW.alerts_enabled_at := statement_timestamp();
    END IF;
  ELSE
    NEW.alerts_enabled_at := NULL;
  END IF;
  RETURN NEW;
END
$compat$;--> statement-breakpoint

CREATE TRIGGER watchlist_alerts_enabled_at_compat_before_write
BEFORE INSERT OR UPDATE OF alerts_enabled, alerts_enabled_at
ON public.watchlist
FOR EACH ROW
EXECUTE FUNCTION public.jobseek_watchlist_alerts_enabled_at_compat();--> statement-breakpoint

ALTER TABLE public.watchlist
  ADD CONSTRAINT watchlist_alerts_enabled_at_check CHECK (
    (alerts_enabled AND alerts_enabled_at IS NOT NULL)
    OR (NOT alerts_enabled AND alerts_enabled_at IS NULL)
  );--> statement-breakpoint

CREATE TABLE public.notification_delivery (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  user_id text NOT NULL,
  cadence public.notification_cadence NOT NULL,
  scheduled_for timestamp with time zone NOT NULL,
  window_start timestamp with time zone NOT NULL,
  window_end timestamp with time zone NOT NULL,
  status public.notification_delivery_status DEFAULT 'pending' NOT NULL,
  match_count integer,
  idempotency_key text NOT NULL,
  provider_message_id text,
  provider_attempt_count integer DEFAULT 0 NOT NULL,
  last_provider_attempt_at timestamp with time zone,
  last_error_code text,
  deferred_until timestamp with time zone,
  completed_at timestamp with time zone,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  updated_at timestamp with time zone DEFAULT now() NOT NULL,
  CONSTRAINT notification_delivery_user_id_user_id_fk
    FOREIGN KEY (user_id) REFERENCES public."user"(id) ON DELETE CASCADE,
  CONSTRAINT notification_delivery_window_check CHECK (
    window_start < window_end AND window_end <= scheduled_for
  ),
  CONSTRAINT notification_delivery_match_count_check CHECK (
    match_count IS NULL OR match_count >= 0
  ),
  CONSTRAINT notification_delivery_attempt_count_check CHECK (
    provider_attempt_count >= 0
  ),
  CONSTRAINT notification_delivery_completion_check CHECK (
    (status IN ('sent', 'skipped') AND completed_at IS NOT NULL)
    OR (status NOT IN ('sent', 'skipped') AND completed_at IS NULL)
  ),
  CONSTRAINT notification_delivery_skipped_check CHECK (
    status <> 'skipped'
    OR (
      match_count = 0
      AND provider_attempt_count = 0
      AND last_provider_attempt_at IS NULL
      AND provider_message_id IS NULL
    )
  ),
  CONSTRAINT notification_delivery_sendable_match_check CHECK (
    status NOT IN ('sent', 'unknown', 'quota_deferred')
    OR match_count > 0
  ),
  CONSTRAINT notification_delivery_provider_attempt_check CHECK (
    status NOT IN ('sent', 'unknown')
    OR (provider_attempt_count > 0 AND last_provider_attempt_at IS NOT NULL)
  ),
  CONSTRAINT notification_delivery_sent_provider_check CHECK (
    status <> 'sent' OR NULLIF(BTRIM(provider_message_id), '') IS NOT NULL
  ),
  CONSTRAINT notification_delivery_deferred_check CHECK (
    (status = 'quota_deferred' AND deferred_until IS NOT NULL)
    OR (status <> 'quota_deferred' AND deferred_until IS NULL)
  )
);--> statement-breakpoint

CREATE UNIQUE INDEX notification_delivery_period_uidx
  ON public.notification_delivery (user_id, cadence, scheduled_for);--> statement-breakpoint
CREATE UNIQUE INDEX notification_delivery_idempotency_key_uidx
  ON public.notification_delivery (idempotency_key);--> statement-breakpoint
CREATE INDEX notification_delivery_due_idx
  ON public.notification_delivery (status, scheduled_for);--> statement-breakpoint
CREATE INDEX notification_delivery_user_window_idx
  ON public.notification_delivery (user_id, window_end);--> statement-breakpoint
