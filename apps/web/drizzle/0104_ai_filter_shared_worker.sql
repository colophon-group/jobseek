-- Shared demand and fixed project-wide execution. No managed Workflow starts.
CREATE TABLE public.ai_filter_work (
  query_version_id uuid NOT NULL REFERENCES public.ai_filter_query_version(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('historical','freshness')),
  watchlist_id uuid NOT NULL REFERENCES public.watchlist(id) ON DELETE CASCADE,
  owner_id text NOT NULL REFERENCES public."user"(id) ON DELETE CASCADE,
  target_offset integer NOT NULL CHECK (target_offset BETWEEN 1 AND 10000),
  generation bigint NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','idle','paused')),
  available_at timestamptz NOT NULL DEFAULT now(),
  lease_owner text,
  lease_expires_at timestamptz,
  reason text,
  attempts integer NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (query_version_id,kind),
  CHECK ((status = 'processing') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))
);
--> statement-breakpoint
CREATE INDEX ai_filter_work_ready_idx ON public.ai_filter_work (available_at,kind) WHERE status <> 'idle';
--> statement-breakpoint
CREATE TABLE public.ai_filter_worker (
  id integer PRIMARY KEY CHECK (id = 1),
  lease_owner text,
  lease_expires_at timestamptz,
  next_tick_at timestamptz NOT NULL DEFAULT now(),
  blocked_until timestamptz,
  reason text,
  failures integer NOT NULL DEFAULT 0,
  next_lane text NOT NULL DEFAULT 'historical' CHECK (next_lane IN ('historical','freshness')),
  CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL))
);
--> statement-breakpoint
INSERT INTO public.ai_filter_worker(id) VALUES (1);
--> statement-breakpoint
-- Fence old deployment-bound Workflow workers without changing saved criteria,
-- revisions, decisions, or usage reservations. Expired cache claims/ambiguous
-- reservations are reconciled conservatively by the existing repository.
UPDATE public.ai_filter_segment SET status='paused_kill', stop_reason='execution_backend_replaced',
  lease_owner=NULL, lease_expires_at=NULL, updated_at=now()
WHERE status IN ('pending','processing');
--> statement-breakpoint
CREATE FUNCTION public.ai_filter_require_shared_worker() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'processing' AND NOT EXISTS (
    SELECT 1 FROM public.ai_filter_worker WHERE id=1
    AND lease_owner=NEW.lease_owner AND lease_expires_at > clock_timestamp()
  ) THEN
    RAISE EXCEPTION 'narrowed_shared_worker_required' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END;
$$;
--> statement-breakpoint
CREATE TRIGGER ai_filter_shared_worker_fence BEFORE INSERT OR UPDATE ON public.ai_filter_segment
FOR EACH ROW EXECUTE FUNCTION public.ai_filter_require_shared_worker();
--> statement-breakpoint
-- Criteria commits create demand in the same transaction. Duplicate requests
-- for unchanged criteria do not requeue idle work.
CREATE FUNCTION public.ai_filter_enqueue_current_revision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE configuration uuid;
BEGIN
  IF TG_TABLE_NAME='ai_filter_query_version' THEN configuration := NEW.configuration_id; ELSE configuration := NEW.id; END IF;
  INSERT INTO public.ai_filter_work (query_version_id,kind,watchlist_id,owner_id,target_offset)
  SELECT q.id,'freshness',c.watchlist_id,c.owner_id,10000
  FROM public.ai_filter_configuration c JOIN public.ai_filter_query_version q
    ON q.configuration_id=c.id AND q.revision=c.current_revision
  WHERE c.id=configuration
    AND c.status='enabled'
  ON CONFLICT (query_version_id,kind) DO NOTHING;
  UPDATE public.ai_filter_work w SET status='idle',lease_owner=NULL,lease_expires_at=NULL,reason=NULL,updated_at=now()
  FROM public.ai_filter_configuration c JOIN public.ai_filter_query_version q
    ON q.configuration_id=c.id AND q.revision=c.current_revision
  WHERE c.id=configuration AND w.watchlist_id=c.watchlist_id
    AND (c.status<>'enabled' OR w.query_version_id<>q.id);
  RETURN NEW;
END;
$$;
--> statement-breakpoint
CREATE TRIGGER ai_filter_query_demand AFTER INSERT ON public.ai_filter_query_version
FOR EACH ROW EXECUTE FUNCTION public.ai_filter_enqueue_current_revision();
--> statement-breakpoint
CREATE TRIGGER ai_filter_revision_demand AFTER UPDATE OF current_revision,status ON public.ai_filter_configuration
FOR EACH ROW EXECUTE FUNCTION public.ai_filter_enqueue_current_revision();
--> statement-breakpoint
-- Existing saved requests also enter the new durable queue without an owner visit.
INSERT INTO public.ai_filter_work (query_version_id,kind,watchlist_id,owner_id,target_offset)
SELECT q.id,'freshness',c.watchlist_id,c.owner_id,10000
FROM public.ai_filter_configuration c JOIN public.ai_filter_query_version q
  ON q.configuration_id=c.id AND q.revision=c.current_revision
WHERE c.status='enabled'
ON CONFLICT (query_version_id,kind) DO NOTHING;
