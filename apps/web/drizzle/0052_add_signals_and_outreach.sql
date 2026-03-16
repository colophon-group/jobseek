CREATE TABLE IF NOT EXISTS "hiring_signal" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"company_id" uuid NOT NULL REFERENCES "company"("id") ON DELETE CASCADE,
	"signal_type" text NOT NULL,
	"signal_text" text NOT NULL,
	"signal_date" timestamp with time zone NOT NULL,
	"source_id" text NOT NULL,
	"score" real NOT NULL DEFAULT 0,
	"reasoning" text,
	"metadata" jsonb DEFAULT '{}'::jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "outreach_draft" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"signal_id" uuid NOT NULL REFERENCES "hiring_signal"("id") ON DELETE CASCADE,
	"contact_name" text NOT NULL,
	"contact_title" text,
	"contact_email" text,
	"subject" text NOT NULL,
	"body" text NOT NULL,
	"status" text DEFAULT 'pending_review' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE INDEX IF NOT EXISTS "idx_hs_company" ON "hiring_signal" ("company_id");
CREATE INDEX IF NOT EXISTS "idx_hs_type" ON "hiring_signal" ("signal_type");
CREATE INDEX IF NOT EXISTS "idx_od_signal" ON "outreach_draft" ("signal_id");
