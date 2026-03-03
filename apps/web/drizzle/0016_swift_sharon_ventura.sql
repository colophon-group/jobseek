CREATE TABLE "company_request" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"input" text NOT NULL,
	"count" integer DEFAULT 1 NOT NULL,
	"resolved_company_id" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "company_request_input_unique" UNIQUE("input")
);
--> statement-breakpoint
ALTER TABLE "company_request" ADD CONSTRAINT "company_request_resolved_company_id_company_id_fk" FOREIGN KEY ("resolved_company_id") REFERENCES "public"."company"("id") ON DELETE no action ON UPDATE no action;