ALTER TABLE "company_request" ADD COLUMN "status" text DEFAULT 'pending' NOT NULL;--> statement-breakpoint
ALTER TABLE "company_request" ADD COLUMN "resolved_job_board_id" uuid;--> statement-breakpoint
ALTER TABLE "company_request" ADD COLUMN "retries" integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE "company_request" ADD COLUMN "max_retries" integer DEFAULT 3 NOT NULL;--> statement-breakpoint
ALTER TABLE "company_request" ADD COLUMN "error_message" text;--> statement-breakpoint
ALTER TABLE "company_request" ADD CONSTRAINT "company_request_resolved_job_board_id_job_board_id_fk" FOREIGN KEY ("resolved_job_board_id") REFERENCES "public"."job_board"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_cr_status" ON "company_request" USING btree ("status") WHERE status = 'pending';