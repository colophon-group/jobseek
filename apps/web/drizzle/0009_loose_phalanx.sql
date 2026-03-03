CREATE TABLE "job_url_queue" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"job_posting_id" uuid NOT NULL,
	"url" text NOT NULL,
	"status" text DEFAULT 'pending' NOT NULL,
	"retries" integer DEFAULT 0,
	"max_retries" integer DEFAULT 3,
	"error_message" text,
	"locked_until" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "job_url_queue_url_unique" UNIQUE("url")
);
--> statement-breakpoint
ALTER TABLE "job_posting" ALTER COLUMN "title" DROP NOT NULL;--> statement-breakpoint
ALTER TABLE "job_url_queue" ADD CONSTRAINT "job_url_queue_job_posting_id_job_posting_id_fk" FOREIGN KEY ("job_posting_id") REFERENCES "public"."job_posting"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_juq_pending" ON "job_url_queue" USING btree ("status","created_at") WHERE status = 'pending';