CREATE TABLE public.ai_filter_configuration (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  watchlist_id uuid NOT NULL,
  owner_id text NOT NULL,
  status text DEFAULT 'enabled' NOT NULL,
  current_revision integer DEFAULT 1 NOT NULL,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  updated_at timestamp with time zone DEFAULT now() NOT NULL,
  disabled_at timestamp with time zone,
  last_caught_up_at timestamp with time zone,
  last_sweep_at timestamp with time zone,
  CONSTRAINT ai_filter_configuration_watchlist_id_unique UNIQUE(watchlist_id),
  CONSTRAINT ai_filter_configuration_revision_check CHECK (current_revision > 0),
  CONSTRAINT ai_filter_configuration_status_check CHECK (status IN ('enabled', 'disabled')),
  CONSTRAINT ai_filter_configuration_disabled_check CHECK ((status = 'disabled') = (disabled_at IS NOT NULL)),
  CONSTRAINT ai_filter_configuration_watchlist_fk FOREIGN KEY (watchlist_id)
    REFERENCES public.watchlist(id) ON DELETE CASCADE,
  CONSTRAINT ai_filter_configuration_owner_fk FOREIGN KEY (owner_id)
    REFERENCES public."user"(id) ON DELETE CASCADE
);--> statement-breakpoint
CREATE INDEX ai_filter_configuration_owner_idx
  ON public.ai_filter_configuration (owner_id);--> statement-breakpoint
CREATE INDEX ai_filter_configuration_sweep_idx
  ON public.ai_filter_configuration (status, last_sweep_at);--> statement-breakpoint

CREATE TABLE public.ai_filter_query_version (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  configuration_id uuid NOT NULL,
  revision integer NOT NULL,
  query_text text NOT NULL,
  normalized_query text NOT NULL,
  model text NOT NULL,
  prompt_version text NOT NULL,
  schema_version text NOT NULL,
  normalizer_version text NOT NULL,
  filter_fingerprint text NOT NULL,
  horizon_started_at timestamp with time zone NOT NULL,
  horizon_ends_at timestamp with time zone NOT NULL,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  CONSTRAINT ai_filter_query_version_revision_check CHECK (revision > 0),
  CONSTRAINT ai_filter_query_version_horizon_check CHECK (horizon_started_at < horizon_ends_at),
  CONSTRAINT ai_filter_query_version_configuration_fk FOREIGN KEY (configuration_id)
    REFERENCES public.ai_filter_configuration(id) ON DELETE CASCADE
);--> statement-breakpoint
CREATE UNIQUE INDEX ai_filter_query_version_revision_uidx
  ON public.ai_filter_query_version (configuration_id, revision);--> statement-breakpoint
CREATE INDEX ai_filter_query_version_configuration_idx
  ON public.ai_filter_query_version (configuration_id, created_at);--> statement-breakpoint

CREATE TABLE public.ai_filter_segment (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  watchlist_id uuid NOT NULL,
  owner_id text NOT NULL,
  query_version_id uuid NOT NULL,
  status text DEFAULT 'pending' NOT NULL,
  selection_offset integer DEFAULT 0 NOT NULL,
  scanned_count integer DEFAULT 0 NOT NULL,
  selection_snapshot jsonb DEFAULT '[]'::jsonb NOT NULL,
  window_start timestamp with time zone NOT NULL,
  window_end timestamp with time zone NOT NULL,
  cursor integer DEFAULT 0 NOT NULL,
  attempt integer DEFAULT 0 NOT NULL,
  lease_owner text,
  lease_expires_at timestamp with time zone,
  stop_reason text,
  idempotency_key text NOT NULL,
  started_at timestamp with time zone,
  completed_at timestamp with time zone,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  updated_at timestamp with time zone DEFAULT now() NOT NULL,
  CONSTRAINT ai_filter_segment_idempotency_key_unique UNIQUE(idempotency_key),
  CONSTRAINT ai_filter_segment_status_check CHECK (status IN (
    'pending', 'processing', 'completed', 'caught_up', 'paused_entitlement', 'paused_budget',
    'paused_provider', 'paused_kill', 'cancelled', 'failed'
  )),
  CONSTRAINT ai_filter_segment_cursor_check CHECK (cursor >= 0 AND cursor <= 50),
  CONSTRAINT ai_filter_segment_offset_check CHECK (selection_offset >= 0 AND scanned_count >= 0 AND attempt >= 0),
  CONSTRAINT ai_filter_segment_window_check CHECK (window_start < window_end),
  CONSTRAINT ai_filter_segment_lease_check CHECK (
    (status = 'processing') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
  ),
  CONSTRAINT ai_filter_segment_watchlist_fk FOREIGN KEY (watchlist_id)
    REFERENCES public.watchlist(id) ON DELETE CASCADE,
  CONSTRAINT ai_filter_segment_owner_fk FOREIGN KEY (owner_id)
    REFERENCES public."user"(id) ON DELETE CASCADE,
  CONSTRAINT ai_filter_segment_query_version_fk FOREIGN KEY (query_version_id)
    REFERENCES public.ai_filter_query_version(id) ON DELETE CASCADE
);--> statement-breakpoint
CREATE INDEX ai_filter_segment_query_idx
  ON public.ai_filter_segment (query_version_id, selection_offset);--> statement-breakpoint
CREATE INDEX ai_filter_segment_resume_idx
  ON public.ai_filter_segment (status, updated_at);--> statement-breakpoint
CREATE UNIQUE INDEX ai_filter_segment_active_watchlist_uidx
  ON public.ai_filter_segment (watchlist_id)
  WHERE status IN ('pending', 'processing', 'paused_entitlement', 'paused_budget', 'paused_provider', 'paused_kill');--> statement-breakpoint

CREATE TABLE public.ai_filter_global_cache (
  cache_key text PRIMARY KEY NOT NULL,
  key_version text NOT NULL,
  content_identity text NOT NULL,
  status text DEFAULT 'pending' NOT NULL,
  decision text,
  model text NOT NULL,
  prompt_version text NOT NULL,
  schema_version text NOT NULL,
  normalizer_version text NOT NULL,
  lease_owner text,
  lease_expires_at timestamp with time zone,
  input_tokens integer,
  output_tokens integer,
  price_version text,
  failure_code text,
  expires_at timestamp with time zone NOT NULL,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  updated_at timestamp with time zone DEFAULT now() NOT NULL,
  CONSTRAINT ai_filter_global_cache_status_check CHECK (status IN ('pending', 'ready', 'failed')),
  CONSTRAINT ai_filter_global_cache_decision_check CHECK (decision IS NULL OR decision IN ('accepted', 'rejected')),
  CONSTRAINT ai_filter_global_cache_key_check CHECK (cache_key ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ai_filter_global_cache_content_check CHECK (content_identity ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ai_filter_global_cache_ready_check CHECK ((status = 'ready') = (decision IS NOT NULL)),
  CONSTRAINT ai_filter_global_cache_lease_check CHECK (
    (status = 'pending') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
  ),
  CONSTRAINT ai_filter_global_cache_usage_check CHECK (
    (input_tokens IS NULL OR input_tokens >= 0) AND
    (output_tokens IS NULL OR output_tokens >= 0)
  )
);--> statement-breakpoint
CREATE INDEX ai_filter_global_cache_expiry_idx
  ON public.ai_filter_global_cache (expires_at);--> statement-breakpoint
CREATE INDEX ai_filter_global_cache_pending_idx
  ON public.ai_filter_global_cache (lease_expires_at)
  WHERE status = 'pending';--> statement-breakpoint

CREATE TABLE public.ai_filter_decision (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  watchlist_id uuid NOT NULL,
  owner_id text NOT NULL,
  query_version_id uuid NOT NULL,
  segment_id uuid NOT NULL,
  candidate_id uuid NOT NULL,
  content_identity text NOT NULL,
  cache_key text NOT NULL,
  model_decision text NOT NULL,
  user_override text,
  posting_first_seen_at timestamp with time zone NOT NULL,
  decided_at timestamp with time zone DEFAULT now() NOT NULL,
  expires_at timestamp with time zone NOT NULL,
  updated_at timestamp with time zone DEFAULT now() NOT NULL,
  CONSTRAINT ai_filter_decision_model_check CHECK (model_decision IN ('accepted', 'rejected')),
  CONSTRAINT ai_filter_decision_override_check CHECK (user_override IS NULL OR user_override IN ('accepted', 'rejected')),
  CONSTRAINT ai_filter_decision_content_check CHECK (content_identity ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ai_filter_decision_retention_check CHECK (expires_at <= posting_first_seen_at + interval '30 days'),
  CONSTRAINT ai_filter_decision_watchlist_fk FOREIGN KEY (watchlist_id)
    REFERENCES public.watchlist(id) ON DELETE CASCADE,
  CONSTRAINT ai_filter_decision_owner_fk FOREIGN KEY (owner_id)
    REFERENCES public."user"(id) ON DELETE CASCADE,
  CONSTRAINT ai_filter_decision_query_version_fk FOREIGN KEY (query_version_id)
    REFERENCES public.ai_filter_query_version(id) ON DELETE CASCADE,
  CONSTRAINT ai_filter_decision_segment_fk FOREIGN KEY (segment_id)
    REFERENCES public.ai_filter_segment(id) ON DELETE CASCADE,
  CONSTRAINT ai_filter_decision_cache_fk FOREIGN KEY (cache_key)
    REFERENCES public.ai_filter_global_cache(cache_key)
);--> statement-breakpoint
CREATE UNIQUE INDEX ai_filter_decision_semantic_uidx
  ON public.ai_filter_decision (
    watchlist_id, query_version_id, candidate_id, content_identity
  );--> statement-breakpoint
CREATE INDEX ai_filter_decision_view_idx
  ON public.ai_filter_decision (
    watchlist_id, query_version_id, model_decision, posting_first_seen_at
  );--> statement-breakpoint
CREATE INDEX ai_filter_decision_expiry_idx
  ON public.ai_filter_decision (expires_at);--> statement-breakpoint

CREATE TABLE public.ai_filter_budget_account (
  scope text NOT NULL,
  scope_key text NOT NULL,
  month_start timestamp with time zone NOT NULL,
  actual_nanodollars bigint DEFAULT 0 NOT NULL,
  reserved_nanodollars bigint DEFAULT 0 NOT NULL,
  updated_at timestamp with time zone DEFAULT now() NOT NULL,
  CONSTRAINT ai_filter_budget_account_pk PRIMARY KEY (scope, scope_key, month_start),
  CONSTRAINT ai_filter_budget_account_scope_check CHECK (scope IN ('user', 'project')),
  CONSTRAINT ai_filter_budget_account_nonnegative_check CHECK (
    actual_nanodollars >= 0 AND reserved_nanodollars >= 0
  ),
  CONSTRAINT ai_filter_budget_account_month_check CHECK (
    month_start = date_trunc('month', month_start)
  )
);--> statement-breakpoint

CREATE TABLE public.ai_filter_usage_ledger (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  idempotency_key text NOT NULL,
  owner_id text NOT NULL,
  segment_id uuid NOT NULL,
  status text DEFAULT 'reserved' NOT NULL,
  month_start timestamp with time zone NOT NULL,
  model text NOT NULL,
  price_version text NOT NULL,
  cache_keys text[] NOT NULL,
  reserved_nanodollars bigint NOT NULL,
  actual_nanodollars bigint DEFAULT 0 NOT NULL,
  input_tokens integer,
  output_tokens integer,
  provider_attempts integer DEFAULT 0 NOT NULL,
  ambiguous_attempts integer DEFAULT 0 NOT NULL,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  reconciled_at timestamp with time zone,
  CONSTRAINT ai_filter_usage_idempotency_key_unique UNIQUE(idempotency_key),
  CONSTRAINT ai_filter_usage_status_check CHECK (status IN ('reserved', 'reconciled', 'released', 'uncertain')),
  CONSTRAINT ai_filter_usage_money_check CHECK (reserved_nanodollars > 0 AND actual_nanodollars >= 0),
  CONSTRAINT ai_filter_usage_tokens_check CHECK (
    (input_tokens IS NULL OR input_tokens >= 0) AND
    (output_tokens IS NULL OR output_tokens >= 0) AND
    provider_attempts >= 0 AND ambiguous_attempts >= 0
  ),
  CONSTRAINT ai_filter_usage_cache_keys_check CHECK (cardinality(cache_keys) BETWEEN 1 AND 5),
  CONSTRAINT ai_filter_usage_reconciled_check CHECK ((status = 'reserved') = (reconciled_at IS NULL)),
  CONSTRAINT ai_filter_usage_owner_fk FOREIGN KEY (owner_id)
    REFERENCES public."user"(id) ON DELETE CASCADE,
  CONSTRAINT ai_filter_usage_segment_fk FOREIGN KEY (segment_id)
    REFERENCES public.ai_filter_segment(id) ON DELETE CASCADE
);--> statement-breakpoint
CREATE INDEX ai_filter_usage_owner_month_idx
  ON public.ai_filter_usage_ledger (owner_id, month_start);--> statement-breakpoint

CREATE TABLE public.ai_filter_event (
  sequence bigserial PRIMARY KEY NOT NULL,
  watchlist_id uuid NOT NULL,
  owner_id text NOT NULL,
  query_version_id uuid NOT NULL,
  type text NOT NULL,
  payload jsonb DEFAULT '{}'::jsonb NOT NULL,
  idempotency_key text NOT NULL,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  CONSTRAINT ai_filter_event_idempotency_key_unique UNIQUE(idempotency_key),
  CONSTRAINT ai_filter_event_watchlist_fk FOREIGN KEY (watchlist_id)
    REFERENCES public.watchlist(id) ON DELETE CASCADE,
  CONSTRAINT ai_filter_event_owner_fk FOREIGN KEY (owner_id)
    REFERENCES public."user"(id) ON DELETE CASCADE,
  CONSTRAINT ai_filter_event_query_version_fk FOREIGN KEY (query_version_id)
    REFERENCES public.ai_filter_query_version(id) ON DELETE CASCADE
);--> statement-breakpoint
CREATE INDEX ai_filter_event_stream_idx
  ON public.ai_filter_event (watchlist_id, sequence);--> statement-breakpoint

CREATE TABLE public.ai_filter_feedback (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  decision_id uuid NOT NULL,
  owner_id text NOT NULL,
  kind text NOT NULL,
  from_decision text,
  to_decision text,
  idempotency_key text NOT NULL,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  CONSTRAINT ai_filter_feedback_idempotency_key_unique UNIQUE(idempotency_key),
  CONSTRAINT ai_filter_feedback_kind_check CHECK (kind IN ('move', 'undo', 'mistake')),
  CONSTRAINT ai_filter_feedback_from_check CHECK (from_decision IS NULL OR from_decision IN ('accepted', 'rejected')),
  CONSTRAINT ai_filter_feedback_to_check CHECK (to_decision IS NULL OR to_decision IN ('accepted', 'rejected')),
  CONSTRAINT ai_filter_feedback_decision_fk FOREIGN KEY (decision_id)
    REFERENCES public.ai_filter_decision(id) ON DELETE CASCADE,
  CONSTRAINT ai_filter_feedback_owner_fk FOREIGN KEY (owner_id)
    REFERENCES public."user"(id) ON DELETE CASCADE
);--> statement-breakpoint
CREATE INDEX ai_filter_feedback_decision_idx
  ON public.ai_filter_feedback (decision_id, created_at);
