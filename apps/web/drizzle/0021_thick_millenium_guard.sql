ALTER TABLE "job_posting_version" DISABLE ROW LEVEL SECURITY;--> statement-breakpoint
DROP TABLE "job_posting_version" CASCADE;--> statement-breakpoint
DROP INDEX "idx_jp_skills";--> statement-breakpoint
DROP INDEX "idx_jp_valid_through";--> statement-breakpoint
ALTER TABLE "job_posting" ALTER COLUMN "source_url" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "job_posting" ALTER COLUMN "first_seen_at" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "job_posting" ALTER COLUMN "created_at" SET DATA TYPE timestamp with time zone;--> statement-breakpoint
ALTER TABLE "job_posting" ALTER COLUMN "created_at" SET DEFAULT now();--> statement-breakpoint
ALTER TABLE "job_posting" ALTER COLUMN "updated_at" SET DATA TYPE timestamp with time zone;--> statement-breakpoint
ALTER TABLE "job_posting" ALTER COLUMN "updated_at" SET DEFAULT now();--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "language" text;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "localizations" jsonb;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "extras" jsonb;--> statement-breakpoint
CREATE INDEX "idx_jp_company" ON "job_posting" USING btree ("company_id");--> statement-breakpoint
CREATE INDEX "idx_jp_board" ON "job_posting" USING btree ("board_id");--> statement-breakpoint
CREATE INDEX "idx_jp_language" ON "job_posting" USING btree ("language");--> statement-breakpoint
ALTER TABLE "job_posting" DROP COLUMN "skills";--> statement-breakpoint
ALTER TABLE "job_posting" DROP COLUMN "valid_through";--> statement-breakpoint
ALTER TABLE "job_posting" DROP COLUMN "responsibilities";--> statement-breakpoint
ALTER TABLE "job_posting" DROP COLUMN "qualifications";--> statement-breakpoint
ALTER TABLE "job_posting" DROP COLUMN "fetch_method";--> statement-breakpoint
ALTER TABLE "job_posting" DROP COLUMN "latest_version_id";