ALTER TABLE public.ai_filter_configuration ADD COLUMN refresh_requested_at timestamp with time zone;
--> statement-breakpoint
ALTER TABLE public.ai_filter_segment ADD COLUMN kind text DEFAULT 'historical' NOT NULL;
--> statement-breakpoint
ALTER TABLE public.ai_filter_segment ADD CONSTRAINT ai_filter_segment_kind_check CHECK (kind IN ('historical', 'freshness'));
--> statement-breakpoint
DROP INDEX public.ai_filter_segment_active_watchlist_uidx;
--> statement-breakpoint
CREATE UNIQUE INDEX ai_filter_segment_active_watchlist_uidx ON public.ai_filter_segment (watchlist_id, kind)
WHERE status IN ('pending', 'processing', 'paused_entitlement', 'paused_budget', 'paused_provider', 'paused_kill');
