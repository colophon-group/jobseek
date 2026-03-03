CREATE TABLE "job_posting_version" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"job_posting_id" uuid NOT NULL,
	"content" jsonb NOT NULL,
	"content_hash" text NOT NULL,
	"fetch_method" text NOT NULL,
	"fetched_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "company" ADD COLUMN "logo" text;--> statement-breakpoint
ALTER TABLE "company" ADD COLUMN "industry" text;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "locations" text[];--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "employment_type" text;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "job_location_type" text;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "base_salary" jsonb;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "skills" text[];--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "date_posted" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "valid_through" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "responsibilities" text[];--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "qualifications" text[];--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "metadata" jsonb DEFAULT '{}'::jsonb;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "fetch_method" text;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "latest_version_id" uuid;--> statement-breakpoint
ALTER TABLE "job_posting_version" ADD CONSTRAINT "job_posting_version_job_posting_id_job_posting_id_fk" FOREIGN KEY ("job_posting_id") REFERENCES "public"."job_posting"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_jpv_posting_fetched" ON "job_posting_version" USING btree ("job_posting_id","fetched_at");--> statement-breakpoint
CREATE UNIQUE INDEX "idx_jpv_posting_hash" ON "job_posting_version" USING btree ("job_posting_id","content_hash");--> statement-breakpoint
CREATE INDEX "idx_jp_locations" ON "job_posting" USING gin ("locations");--> statement-breakpoint
CREATE INDEX "idx_jp_skills" ON "job_posting" USING gin ("skills");--> statement-breakpoint
CREATE INDEX "idx_jp_employment_type" ON "job_posting" USING btree ("employment_type");--> statement-breakpoint
CREATE INDEX "idx_jp_status_active" ON "job_posting" USING btree ("status") WHERE status = 'active';--> statement-breakpoint
CREATE INDEX "idx_jp_last_seen_active" ON "job_posting" USING btree ("last_seen_at") WHERE status = 'active';--> statement-breakpoint
CREATE INDEX "idx_jp_valid_through" ON "job_posting" USING btree ("valid_through") WHERE valid_through IS NOT NULL;--> statement-breakpoint
UPDATE "job_posting" SET "locations" = ARRAY["location"] WHERE "location" IS NOT NULL;--> statement-breakpoint
ALTER TABLE "job_posting" DROP COLUMN "location";