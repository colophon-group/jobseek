-- Retain the exact public B0 configuration witness needed by live joint owners.
CREATE UNIQUE INDEX crawler_ownership_transition_one_reserved_plan
ON public.crawler_ownership_transition(reserved_plan_sha256)
WHERE reserved_plan_sha256 IS NOT NULL;

CREATE TABLE public.crawler_ownership_b0_target (
    target_sha256 text PRIMARY KEY CHECK (target_sha256 ~ '^[0-9a-f]{64}$'),
    payload text NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 16384),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (target_sha256 = encode(sha256(convert_to(payload,'UTF8')),'hex')),
    CHECK ((payload::jsonb ->> 'version' = 'jobseek.crawler.cold-b0-target/v1') IS TRUE),
    CHECK ((jsonb_typeof(payload::jsonb -> 'boards') = 'array') IS TRUE),
    CHECK ((jsonb_array_length(payload::jsonb -> 'boards') BETWEEN 1 AND 16) IS TRUE)
);
CREATE FUNCTION public.jobseek_crawler_ownership_b0_target_retained()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'crawler_ownership_target_retained';
END;
$$;
CREATE TRIGGER crawler_ownership_b0_target_retained
BEFORE UPDATE OR DELETE ON public.crawler_ownership_b0_target
FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_ownership_b0_target_retained();
