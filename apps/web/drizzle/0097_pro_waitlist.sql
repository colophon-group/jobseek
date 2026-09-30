CREATE TABLE "pro_waitlist" (
  "email" text PRIMARY KEY NOT NULL,
  "locale" text DEFAULT 'en' NOT NULL,
  "created_at" timestamptz DEFAULT now() NOT NULL,
  CONSTRAINT "pro_waitlist_email_normalized" CHECK ("email" = lower(btrim("email")) AND char_length("email") BETWEEN 3 AND 254),
  CONSTRAINT "pro_waitlist_locale_valid" CHECK ("locale" IN ('en', 'de', 'fr', 'it'))
);
--> statement-breakpoint
-- Signup is handled by the rate-limited server action, never the Data API.
ALTER TABLE public.pro_waitlist ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.pro_waitlist FROM PUBLIC;
DO $$
DECLARE browser_role text;
BEGIN
  FOR browser_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated') LOOP
    EXECUTE format('REVOKE ALL ON TABLE public.pro_waitlist FROM %I', browser_role);
  END LOOP;
END $$;
