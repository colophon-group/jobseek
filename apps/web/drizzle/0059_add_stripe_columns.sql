ALTER TABLE "subscription"
  ADD COLUMN "stripe_customer_id" text,
  ADD COLUMN "stripe_subscription_id" text;

CREATE UNIQUE INDEX "idx_sub_stripe_customer"
  ON "subscription" ("stripe_customer_id")
  WHERE stripe_customer_id IS NOT NULL;

CREATE UNIQUE INDEX "idx_sub_stripe_subscription"
  ON "subscription" ("stripe_subscription_id")
  WHERE stripe_subscription_id IS NOT NULL;

CREATE UNIQUE INDEX "idx_sub_user"
  ON "subscription" ("user_id");
