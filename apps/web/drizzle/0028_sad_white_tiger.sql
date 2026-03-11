ALTER TABLE "company_request" DROP CONSTRAINT "company_request_resolved_job_board_id_job_board_id_fk";
--> statement-breakpoint
ALTER TABLE "company_request" ADD CONSTRAINT "company_request_resolved_job_board_id_job_board_id_fk" FOREIGN KEY ("resolved_job_board_id") REFERENCES "public"."job_board"("id") ON DELETE set null ON UPDATE no action;