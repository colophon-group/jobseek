-- Existing accounts remain opted out: this migration creates an empty event
-- history, with no backfill from accounts, billing, cookies or job alerts.
CREATE TABLE public.product_news_consent (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  sequence bigserial NOT NULL UNIQUE,
  user_id text NOT NULL REFERENCES public."user"(id) ON DELETE CASCADE,
  email text NOT NULL,
  enabled boolean NOT NULL,
  locale text NOT NULL,
  source text NOT NULL,
  consent_version text NOT NULL,
  consent_text text NOT NULL,
  created_at timestamptz DEFAULT now() NOT NULL,
  CONSTRAINT product_news_consent_email_normalized CHECK (email = lower(btrim(email))),
  CONSTRAINT product_news_consent_locale_valid CHECK (locale IN ('en', 'de', 'fr', 'it')),
  CONSTRAINT product_news_consent_source_valid CHECK (source IN ('signup', 'settings', 'unsubscribe')),
  CONSTRAINT product_news_consent_source_choice CHECK ((source <> 'signup' OR enabled = true) AND (source <> 'unsubscribe' OR enabled = false))
);
--> statement-breakpoint
CREATE INDEX product_news_consent_user_sequence_idx ON public.product_news_consent (user_id, sequence DESC);
--> statement-breakpoint
ALTER TABLE public.product_news_consent ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.product_news_consent FROM PUBLIC;
REVOKE ALL ON SEQUENCE public.product_news_consent_sequence_seq FROM PUBLIC;
DO $$
DECLARE browser_role text;
BEGIN
  FOR browser_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated') LOOP
    EXECUTE format('REVOKE ALL ON TABLE public.product_news_consent FROM %I', browser_role);
    EXECUTE format('REVOKE ALL ON SEQUENCE public.product_news_consent_sequence_seq FROM %I', browser_role);
  END LOOP;
END $$;
