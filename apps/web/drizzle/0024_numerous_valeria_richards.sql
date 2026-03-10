ALTER TABLE "job_url_queue" DISABLE ROW LEVEL SECURITY;--> statement-breakpoint
DROP TABLE "job_url_queue" CASCADE;--> statement-breakpoint
DROP INDEX "idx_jb_next_check";--> statement-breakpoint
ALTER TABLE "job_board" DROP COLUMN "attempt_count";