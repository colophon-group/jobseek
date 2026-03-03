CREATE TABLE "job_board" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"company_id" uuid NOT NULL,
	"crawler_type" text NOT NULL,
	"board_url" text NOT NULL,
	"check_interval_minutes" integer DEFAULT 60 NOT NULL,
	"next_check_at" timestamp with time zone DEFAULT now() NOT NULL,
	"last_checked_at" timestamp with time zone,
	"last_success_at" timestamp with time zone,
	"consecutive_failures" integer DEFAULT 0 NOT NULL,
	"last_error" text,
	"is_enabled" boolean DEFAULT true NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "job_board_board_url_unique" UNIQUE("board_url")
);
--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "board_id" uuid;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "source_url" text;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "status" text DEFAULT 'active' NOT NULL;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "first_seen_at" timestamp with time zone DEFAULT now();--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "last_seen_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "job_posting" ADD COLUMN "delisted_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "job_board" ADD CONSTRAINT "job_board_company_id_company_id_fk" FOREIGN KEY ("company_id") REFERENCES "public"."company"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_jb_next_check" ON "job_board" USING btree ("next_check_at");--> statement-breakpoint
CREATE INDEX "idx_jb_company" ON "job_board" USING btree ("company_id");--> statement-breakpoint
ALTER TABLE "job_posting" ADD CONSTRAINT "job_posting_board_id_job_board_id_fk" FOREIGN KEY ("board_id") REFERENCES "public"."job_board"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "job_posting" ADD CONSTRAINT "job_posting_source_url_unique" UNIQUE("source_url");