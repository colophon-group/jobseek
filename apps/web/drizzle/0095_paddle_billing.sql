CREATE TABLE "paddle_account" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "user_id" text NOT NULL REFERENCES "user"("id") ON DELETE CASCADE,
  "environment" text NOT NULL CHECK ("environment" IN ('sandbox', 'production')),
  "customer_id" text,
  "pending_transaction_id" text,
  "pending_price_id" text,
  "trial_used_at" timestamptz,
  "deletion_requested" boolean DEFAULT false NOT NULL,
  "created_at" timestamptz DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX "idx_paddle_account_user_environment" ON "paddle_account" ("user_id", "environment");
CREATE UNIQUE INDEX "idx_paddle_account_customer_environment" ON "paddle_account" ("customer_id", "environment");
CREATE UNIQUE INDEX "idx_paddle_account_transaction" ON "paddle_account" ("pending_transaction_id");
--> statement-breakpoint
CREATE TABLE "paddle_subscription" (
  "id" text PRIMARY KEY NOT NULL,
  "account_id" uuid NOT NULL REFERENCES "paddle_account"("id") ON DELETE CASCADE,
  "status" text NOT NULL,
  "price_id" text,
  "expected_price_id" text NOT NULL,
  "entitled" boolean DEFAULT false NOT NULL,
  "current_period_end" timestamptz,
  "scheduled_cancel_at" timestamptz,
  "event_occurred_at" timestamptz NOT NULL,
  "updated_at" timestamptz DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE INDEX "idx_paddle_subscription_account" ON "paddle_subscription" ("account_id");
