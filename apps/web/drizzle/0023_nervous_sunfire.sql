ALTER TABLE "job_board" ADD COLUMN "board_status" text DEFAULT 'active' NOT NULL;--> statement-breakpoint
ALTER TABLE "job_board" ADD COLUMN "throttle_key" text;--> statement-breakpoint
ALTER TABLE "job_board" ADD COLUMN "lease_owner" text;--> statement-breakpoint
ALTER TABLE "job_board" ADD COLUMN "leased_until" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "job_board" ADD COLUMN "attempt_count" integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE "job_board" ADD COLUMN "empty_check_count" integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE "job_board" ADD COLUMN "last_non_empty_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "job_board" ADD COLUMN "gone_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "delist_reason" text;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "relisted_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "next_scrape_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "last_scraped_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "scrape_interval_hours" integer DEFAULT 24 NOT NULL;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "lease_owner" text;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "leased_until" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "scrape_failures" integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "last_scrape_error" text;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "missing_count" integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "scrape_domain" text;--> statement-breakpoint
CREATE INDEX "idx_jb_due" ON "job_board" USING btree ("next_check_at","throttle_key") WHERE board_status IN ('active', 'suspect') AND is_enabled = true;--> statement-breakpoint
CREATE INDEX "idx_jb_lease" ON "job_board" USING btree ("leased_until") WHERE leased_until IS NOT NULL;--> statement-breakpoint
CREATE INDEX "idx_jp_next_scrape" ON "job_posting" USING btree ("next_scrape_at") WHERE status = 'active' AND next_scrape_at IS NOT NULL;--> statement-breakpoint
CREATE INDEX "idx_jp_lease" ON "job_posting" USING btree ("leased_until") WHERE leased_until IS NOT NULL;--> statement-breakpoint

-- Backfill: set board_status = 'disabled' for boards that are currently disabled
UPDATE job_board SET board_status = 'disabled' WHERE is_enabled = false;--> statement-breakpoint

-- Backfill: populate throttle_key from crawler_type / board_url.
-- Rich/API monitor types use crawler_type as the throttle key (they share an API host);
-- URL-only monitors (sitemap, dom, nextdata, etc.) use the hostname from board_url.
UPDATE job_board
SET throttle_key = CASE
    WHEN crawler_type IN (
        'ashby', 'bite', 'breezy', 'dvinci', 'gem', 'greenhouse',
        'hireology', 'join', 'lever', 'pinpoint', 'recruitee',
        'rippling', 'rss', 'smartrecruiters', 'softgarden',
        'traffit', 'workable', 'workday'
    ) THEN crawler_type
    ELSE split_part(split_part(board_url, '://', 2), '/', 1)
END
WHERE throttle_key IS NULL;