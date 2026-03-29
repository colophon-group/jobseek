-- Crypto-payment-gated API credits
-- One row per on-chain payment. Token is the bearer key the agent uses.
CREATE TABLE "api_credit" (
  "id"              uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
  "token"           text        NOT NULL UNIQUE,          -- bearer token returned to agent
  "wallet_address"  text        NOT NULL,                 -- payer's EOA
  "tx_hash"         text        NOT NULL UNIQUE,          -- Sepolia tx that funded this credit
  "chain_id"        integer     NOT NULL DEFAULT 11155111, -- 11155111 = Sepolia
  "amount_wei"      text        NOT NULL,                 -- value in wei (stored as text to avoid bigint overflow)
  "credits_granted" integer     NOT NULL,
  "credits_used"    integer     NOT NULL DEFAULT 0,
  "created_at"      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX "idx_api_credit_token"          ON "api_credit" ("token");
CREATE INDEX "idx_api_credit_wallet_address" ON "api_credit" ("wallet_address");
