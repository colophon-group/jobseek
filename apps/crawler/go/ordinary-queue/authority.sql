-- Retained attempt authority for native ordinary monitor/detail transactions.
-- Ready queues and routing epoch allocation remain the existing authorities.
CREATE TABLE public.ordinary_worker_write_fence (
    task_kind text NOT NULL CHECK (task_kind IN ('monitor', 'scrape')),
    task_id uuid NOT NULL,
    board_id uuid NOT NULL REFERENCES public.job_board(id) ON DELETE CASCADE,
    routing_epoch bigint NOT NULL CHECK (routing_epoch BETWEEN 1 AND 9999999999999),
    claim_token text NOT NULL CHECK (claim_token ~ '^[0-9a-f]{32}$'),
    config_sha256 text NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    state text NOT NULL CHECK (state IN ('active', 'completed')),
    next_due_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (task_kind, task_id),
    CHECK (task_kind <> 'monitor' OR task_id = board_id),
    CHECK (state = 'completed' OR next_due_at IS NULL)
);

-- Every persisted native fence also participates in global epoch retirement.
-- Lock order is lease barrier, then routing barrier, then canonical/fence rows.
CREATE FUNCTION public.jobseek_ordinary_worker_enforce_epoch()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
PARALLEL UNSAFE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    current_epoch bigint;
    allocator_called boolean;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(7544422533504811010);
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(7544422533504811009);
    SELECT last_value, is_called INTO current_epoch, allocator_called
    FROM public.lightpanda_b0_routing_epoch_seq;
    IF allocator_called IS DISTINCT FROM true
        OR current_epoch IS DISTINCT FROM NEW.routing_epoch
    THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001',
            MESSAGE = 'ordinary_worker_write_fence_rejected',
            DETAIL = 'routing_epoch_not_current';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER ordinary_worker_current_routing_epoch
BEFORE INSERT OR UPDATE ON public.ordinary_worker_write_fence
FOR EACH ROW EXECUTE FUNCTION public.jobseek_ordinary_worker_enforce_epoch();
