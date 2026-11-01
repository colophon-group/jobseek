-- Running upgrade 0041 -> 0042

CREATE TABLE public.source_coverage_scope (
    board_id uuid PRIMARY KEY REFERENCES public.job_board(id) ON DELETE CASCADE,
    revision uuid NOT NULL DEFAULT gen_random_uuid(),
    tracked_since timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO public.source_coverage_scope(board_id)
SELECT id FROM public.job_board;

CREATE TABLE public.source_coverage_check (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id uuid NOT NULL REFERENCES public.job_board(id) ON DELETE CASCADE,
    revision uuid NOT NULL,
    attempt_hash char(64) NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    next_due_at timestamptz NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('complete', 'partial', 'failed', 'gone')),
    CHECK (started_at <= finished_at),
    UNIQUE (board_id, attempt_hash)
);
CREATE INDEX source_coverage_check_window
ON public.source_coverage_check(board_id, revision, finished_at);

CREATE TABLE public.source_coverage_posting_first (
    posting_id uuid PRIMARY KEY,
    board_id uuid NOT NULL REFERENCES public.job_board(id) ON DELETE CASCADE,
    revision uuid NOT NULL,
    first_observed_at timestamptz NOT NULL
);
CREATE INDEX source_coverage_posting_window
ON public.source_coverage_posting_first(board_id, revision, first_observed_at);

CREATE OR REPLACE FUNCTION public.jobseek_source_coverage_board_revision()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO public.source_coverage_scope(board_id) VALUES (NEW.id);
    ELSIF OLD.company_id IS DISTINCT FROM NEW.company_id
       OR OLD.board_slug IS DISTINCT FROM NEW.board_slug
       OR OLD.board_url IS DISTINCT FROM NEW.board_url
       OR OLD.crawler_type IS DISTINCT FROM NEW.crawler_type
       OR OLD.check_interval_minutes IS DISTINCT FROM NEW.check_interval_minutes
       OR OLD.scrape_interval_hours IS DISTINCT FROM NEW.scrape_interval_hours
       OR OLD.throttle_key IS DISTINCT FROM NEW.throttle_key
       OR OLD.monitor_needs_browser IS DISTINCT FROM NEW.monitor_needs_browser
       OR OLD.scraper_needs_browser IS DISTINCT FROM NEW.scraper_needs_browser
       OR OLD.is_enabled IS DISTINCT FROM NEW.is_enabled
       OR OLD.board_status IS DISTINCT FROM NEW.board_status
       OR (OLD.metadata ->> '_monitor_config_fingerprint')
          IS DISTINCT FROM (NEW.metadata ->> '_monitor_config_fingerprint')
    THEN
        UPDATE public.source_coverage_scope
        SET revision = gen_random_uuid(), tracked_since = clock_timestamp()
        WHERE board_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER source_coverage_board_revision
AFTER INSERT OR UPDATE ON public.job_board
FOR EACH ROW EXECUTE FUNCTION public.jobseek_source_coverage_board_revision();

CREATE OR REPLACE FUNCTION public.jobseek_source_coverage_posting_first()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.board_id IS NOT NULL THEN
            INSERT INTO public.source_coverage_posting_first
                (posting_id, board_id, revision, first_observed_at)
            SELECT NEW.id, scope.board_id, scope.revision, NEW.first_seen_at
            FROM public.source_coverage_scope AS scope
            WHERE scope.board_id = NEW.board_id;
        END IF;
    ELSIF NEW.first_seen_at < OLD.first_seen_at THEN
        UPDATE public.source_coverage_posting_first
        SET first_observed_at = LEAST(first_observed_at, NEW.first_seen_at)
        WHERE posting_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER source_coverage_posting_first
AFTER INSERT OR UPDATE OF first_seen_at ON public.job_posting
FOR EACH ROW EXECUTE FUNCTION public.jobseek_source_coverage_posting_first();;

UPDATE alembic_version SET version_num='0042' WHERE alembic_version.version_num = '0041';
