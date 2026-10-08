CREATE TABLE public."mcp_feedback" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "kind" text NOT NULL CONSTRAINT mcp_feedback_kind_valid CHECK ("kind" IN ('bug', 'feature', 'feedback')),
  "payload" jsonb NOT NULL CONSTRAINT mcp_feedback_payload_object CHECK (jsonb_typeof("payload") = 'object'),
  "server_version" text NOT NULL,
  "consumer" text NOT NULL CONSTRAINT mcp_feedback_consumer_valid CHECK ("consumer" IN ('external', 'hosted_mcp')),
  "created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE public.mcp_feedback ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.mcp_feedback FROM PUBLIC;
DO $$
DECLARE browser_role text;
BEGIN
  FOR browser_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated') LOOP
    EXECUTE format('REVOKE ALL ON TABLE public.mcp_feedback FROM %I', browser_role);
  END LOOP;
END $$;
