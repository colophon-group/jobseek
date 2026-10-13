-- Running upgrade 0025 -> 0026

ALTER TABLE public.lightpanda_b0_write_fence DROP CONSTRAINT lightpanda_b0_write_fence_engine_owner_check;

ALTER TABLE public.lightpanda_b0_write_fence ADD CONSTRAINT lightpanda_b0_write_fence_engine_owner_check CHECK (engine_owner IN ('python', 'go'));

CREATE OR REPLACE FUNCTION public.jobseek_lightpanda_b0_require_write_fence(
    supplied_job_posting_id uuid,
    supplied_shard_id text,
    supplied_routing_epoch bigint,
    supplied_engine_owner text,
    supplied_config_revision bigint,
    supplied_payload_sha256 text,
    supplied_claim_token text
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
PARALLEL UNSAFE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    current_fence public.lightpanda_b0_write_fence%ROWTYPE;
    supplied_sequence bigint;
BEGIN
    IF supplied_job_posting_id IS NULL
        OR supplied_shard_id IS NULL
        OR supplied_shard_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
        OR supplied_engine_owner NOT IN ('python', 'go')
        OR supplied_config_revision IS NULL
        OR supplied_config_revision NOT BETWEEN 1 AND 9999999999999
        OR supplied_payload_sha256 IS NULL
        OR supplied_payload_sha256 !~ '^[0-9a-f]{64}$'
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'invalid_identity';
    END IF;
    supplied_sequence := public.jobseek_lightpanda_b0_claim_sequence(
        supplied_claim_token,
        supplied_routing_epoch
    );

    SELECT *
    INTO current_fence
    FROM public.lightpanda_b0_write_fence
    WHERE job_posting_id = supplied_job_posting_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'missing';
    ELSIF current_fence.state IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'revoked';
    ELSIF current_fence.shard_id IS DISTINCT FROM supplied_shard_id THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'shard_id_mismatch';
    ELSIF current_fence.routing_epoch IS DISTINCT FROM supplied_routing_epoch THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'routing_epoch_mismatch';
    ELSIF current_fence.engine_owner IS DISTINCT FROM supplied_engine_owner THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'engine_owner_mismatch';
    ELSIF current_fence.config_revision IS DISTINCT FROM supplied_config_revision THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'config_revision_mismatch';
    ELSIF current_fence.payload_sha256 IS DISTINCT FROM supplied_payload_sha256 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'payload_digest_mismatch';
    ELSIF current_fence.claim_token IS DISTINCT FROM supplied_claim_token
        OR current_fence.claim_sequence IS DISTINCT FROM supplied_sequence
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'claim_token_mismatch';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.jobseek_lightpanda_b0_activate_write_fence(
    supplied_job_posting_id uuid,
    supplied_shard_id text,
    supplied_routing_epoch bigint,
    supplied_engine_owner text,
    supplied_config_revision bigint,
    supplied_payload_sha256 text,
    supplied_claim_token text
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
PARALLEL UNSAFE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    current_fence public.lightpanda_b0_write_fence%ROWTYPE;
    supplied_sequence bigint;
    inserted_count bigint;
BEGIN
    IF supplied_job_posting_id IS NULL
        OR supplied_shard_id IS NULL
        OR supplied_shard_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
        OR supplied_engine_owner NOT IN ('python', 'go')
        OR supplied_config_revision IS NULL
        OR supplied_config_revision NOT BETWEEN 1 AND 9999999999999
        OR supplied_payload_sha256 IS NULL
        OR supplied_payload_sha256 !~ '^[0-9a-f]{64}$'
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'invalid_identity';
    END IF;
    supplied_sequence := public.jobseek_lightpanda_b0_claim_sequence(
        supplied_claim_token,
        supplied_routing_epoch
    );

    BEGIN
        INSERT INTO public.lightpanda_b0_write_fence (
            job_posting_id,
            shard_id,
            routing_epoch,
            engine_owner,
            config_revision,
            payload_sha256,
            claim_token,
            claim_sequence,
            state
        ) VALUES (
            supplied_job_posting_id,
            supplied_shard_id,
            supplied_routing_epoch,
            supplied_engine_owner,
            supplied_config_revision,
            supplied_payload_sha256,
            supplied_claim_token,
            supplied_sequence,
            'active'
        )
        ON CONFLICT (job_posting_id) DO NOTHING;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
    EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'generation_in_use';
    END;
    IF inserted_count = 1 THEN
        RETURN;
    END IF;

    SELECT *
    INTO STRICT current_fence
    FROM public.lightpanda_b0_write_fence
    WHERE job_posting_id = supplied_job_posting_id
    FOR UPDATE;

    IF current_fence.shard_id IS NOT DISTINCT FROM supplied_shard_id
        AND current_fence.routing_epoch IS NOT DISTINCT FROM supplied_routing_epoch
        AND current_fence.engine_owner IS NOT DISTINCT FROM supplied_engine_owner
        AND current_fence.config_revision IS NOT DISTINCT FROM supplied_config_revision
        AND current_fence.payload_sha256 IS NOT DISTINCT FROM supplied_payload_sha256
        AND current_fence.claim_token IS NOT DISTINCT FROM supplied_claim_token
        AND current_fence.claim_sequence IS NOT DISTINCT FROM supplied_sequence
    THEN
        IF current_fence.state = 'active' THEN
            RETURN;
        END IF;
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'revoked_replay';
    END IF;

    IF supplied_routing_epoch < current_fence.routing_epoch
        OR (
            supplied_routing_epoch = current_fence.routing_epoch
            AND supplied_sequence <= current_fence.claim_sequence
        )
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'generation_not_advanced';
    ELSIF supplied_routing_epoch = current_fence.routing_epoch
        AND (supplied_shard_id IS DISTINCT FROM current_fence.shard_id OR supplied_engine_owner IS DISTINCT FROM current_fence.engine_owner)
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'route_change_requires_epoch';
    ELSIF supplied_config_revision < current_fence.config_revision THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'config_revision_not_monotonic';
    ELSIF supplied_config_revision = current_fence.config_revision
        AND supplied_payload_sha256 IS DISTINCT FROM current_fence.payload_sha256
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'payload_change_requires_config_revision';
    END IF;

    BEGIN
        UPDATE public.lightpanda_b0_write_fence
        SET shard_id = supplied_shard_id,
            routing_epoch = supplied_routing_epoch,
            engine_owner = supplied_engine_owner,
            config_revision = supplied_config_revision,
            payload_sha256 = supplied_payload_sha256,
            claim_token = supplied_claim_token,
            claim_sequence = supplied_sequence,
            state = 'active'
        WHERE job_posting_id = supplied_job_posting_id;
    EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'lightpanda_b0_write_fence_rejected',
            DETAIL = 'generation_in_use';
    END;
END;
$$;

CREATE OR REPLACE FUNCTION public.jobseek_lightpanda_b0_revoke_write_fence(
    supplied_job_posting_id uuid,
    supplied_shard_id text,
    supplied_routing_epoch bigint,
    supplied_engine_owner text,
    supplied_config_revision bigint,
    supplied_payload_sha256 text,
    supplied_claim_token text
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
PARALLEL UNSAFE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM public.jobseek_lightpanda_b0_require_write_fence(
        supplied_job_posting_id,
        supplied_shard_id,
        supplied_routing_epoch,
        supplied_engine_owner,
        supplied_config_revision,
        supplied_payload_sha256,
        supplied_claim_token
    );
    UPDATE public.lightpanda_b0_write_fence
    SET state = 'revoked'
    WHERE job_posting_id = supplied_job_posting_id;
END;
$$;;

UPDATE alembic_version SET version_num='0026' WHERE alembic_version.version_num = '0025';
