CREATE TABLE "company_resolve_queue" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"input" text NOT NULL,
	"status" text DEFAULT 'pending' NOT NULL,
	"resolved_company_id" uuid,
	"resolved_job_board_id" uuid,
	"retries" integer DEFAULT 0 NOT NULL,
	"max_retries" integer DEFAULT 3 NOT NULL,
	"error_message" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "company_resolve_queue" ADD CONSTRAINT "company_resolve_queue_resolved_company_id_company_id_fk" FOREIGN KEY ("resolved_company_id") REFERENCES "public"."company"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "company_resolve_queue" ADD CONSTRAINT "company_resolve_queue_resolved_job_board_id_job_board_id_fk" FOREIGN KEY ("resolved_job_board_id") REFERENCES "public"."job_board"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_crq_pending" ON "company_resolve_queue" USING btree ("status","created_at") WHERE status = 'pending';