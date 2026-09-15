-- Better Auth 1.7.3 restored account identity to (provider_id, account_id).
-- Releases 1.7.3+ no longer write issuer, so remove the temporary 1.7.0-1.7.2
-- constraints while retaining the populated column for reversible cleanup.
DROP TRIGGER IF EXISTS account_issuer_compat_before_write ON public.account;--> statement-breakpoint
DROP FUNCTION IF EXISTS public.jobseek_better_auth_account_issuer_compat();--> statement-breakpoint
DROP INDEX IF EXISTS public.account_issuer_account_id_uidx;--> statement-breakpoint
ALTER TABLE public.account ALTER COLUMN issuer DROP NOT NULL;
