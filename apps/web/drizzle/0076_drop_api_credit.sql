-- Drop the api_credit table (#2795).
--
-- Backstory: 0072_add_api_credits + 0073_drop_api_credits was an
-- author/release-and-revert pair from a never-shipped agentic-API-
-- credits experiment. Only the *add* half ran on prod (table exists
-- with 6 rows of test/payment data); the drop never landed because
-- both files sat as unjournaled orphans on disk. After user-explicit
-- direction in #2795 ("drop api_credit"), the cancel-out pair has
-- been removed from the filesystem and this single idempotent drop
-- replaces them.
--
-- Idempotent on purpose: this file is now journaled at idx=67 with
-- the tag `0076_drop_api_credit`. On every existing prod (where the
-- table exists today) it actually drops; on any fresh database where
-- the table never existed, IF EXISTS is a no-op.

DROP TABLE IF EXISTS "api_credit";
