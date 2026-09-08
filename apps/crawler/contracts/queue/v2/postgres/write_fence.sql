-- Inactive queue-v2 PostgreSQL write-fence candidate. Never migrated or
-- imported by the production crawler.

CREATE SCHEMA queue_v2_contract;

CREATE FUNCTION queue_v2_contract.jobseek_queue_v2_claim_sequence(
    supplied_claim_token text,
    supplied_routing_epoch bigint
)
RETURNS bigint
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    token_epoch bigint;
    token_sequence bigint;
BEGIN
    IF supplied_routing_epoch IS NULL
        OR supplied_routing_epoch NOT BETWEEN 1 AND 9999999999999
        OR supplied_claim_token IS NULL
        OR supplied_claim_token !~ '^[1-9][0-9]{0,12}:[1-9][0-9]{0,12}$'
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'invalid_claim_token';
    END IF;

    token_epoch := split_part(supplied_claim_token, ':', 1)::bigint;
    token_sequence := split_part(supplied_claim_token, ':', 2)::bigint;
    IF token_epoch IS DISTINCT FROM supplied_routing_epoch
        OR token_sequence NOT BETWEEN 1 AND 9999999999999
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'invalid_claim_token';
    END IF;
    RETURN token_sequence;
END;
$$;

CREATE TABLE queue_v2_contract.queue_v2_write_fence (
    task_kind text NOT NULL CHECK (task_kind <> ''),
    task_id text NOT NULL CHECK (task_id <> ''),
    shard_id text NOT NULL CHECK (shard_id <> ''),
    routing_epoch bigint NOT NULL
        CHECK (routing_epoch BETWEEN 1 AND 9999999999999),
    engine_owner text NOT NULL
        CHECK (engine_owner IN ('python', 'go')),
    config_revision bigint NOT NULL
        CHECK (config_revision BETWEEN 1 AND 9999999999999),
    claim_token text NOT NULL,
    claim_sequence bigint NOT NULL
        CHECK (claim_sequence BETWEEN 1 AND 9999999999999),
    state text NOT NULL
        CHECK (state IN ('inflight', 'revoked')),
    PRIMARY KEY (task_kind, task_id),
    UNIQUE (
        task_kind,
        shard_id,
        routing_epoch,
        engine_owner,
        claim_sequence
    ),
    CHECK (
        claim_sequence =
        queue_v2_contract.jobseek_queue_v2_claim_sequence(
            claim_token,
            routing_epoch
        )
    )
);

CREATE FUNCTION queue_v2_contract.jobseek_queue_v2_require_write_fence(
    supplied_task_kind text,
    supplied_task_id text,
    supplied_shard_id text,
    supplied_routing_epoch bigint,
    supplied_engine_owner text,
    supplied_config_revision bigint,
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
    current_fence queue_v2_contract.queue_v2_write_fence%ROWTYPE;
    supplied_sequence bigint;
BEGIN
    IF supplied_task_kind IS NULL OR supplied_task_kind = ''
        OR supplied_task_id IS NULL OR supplied_task_id = ''
        OR supplied_shard_id IS NULL OR supplied_shard_id = ''
        OR supplied_engine_owner IS NULL
        OR supplied_engine_owner NOT IN ('python', 'go')
        OR supplied_config_revision IS NULL
        OR supplied_config_revision NOT BETWEEN 1 AND 9999999999999
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'invalid_identity';
    END IF;
    supplied_sequence :=
        queue_v2_contract.jobseek_queue_v2_claim_sequence(
            supplied_claim_token,
            supplied_routing_epoch
        );

    SELECT *
    INTO current_fence
    FROM queue_v2_contract.queue_v2_write_fence
    WHERE task_kind = supplied_task_kind
        AND task_id = supplied_task_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'missing';
    ELSIF current_fence.state IS DISTINCT FROM 'inflight' THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'inactive';
    ELSIF current_fence.shard_id IS DISTINCT FROM supplied_shard_id THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'shard_id_mismatch';
    ELSIF current_fence.routing_epoch IS DISTINCT FROM supplied_routing_epoch THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'routing_epoch_mismatch';
    ELSIF current_fence.engine_owner IS DISTINCT FROM supplied_engine_owner THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'engine_owner_mismatch';
    ELSIF current_fence.config_revision IS DISTINCT FROM supplied_config_revision THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'config_revision_mismatch';
    ELSIF current_fence.claim_token IS DISTINCT FROM supplied_claim_token
        OR current_fence.claim_sequence IS DISTINCT FROM supplied_sequence
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'claim_token_mismatch';
    END IF;
END;
$$;

CREATE FUNCTION queue_v2_contract.jobseek_queue_v2_activate_write_fence(
    supplied_task_kind text,
    supplied_task_id text,
    supplied_shard_id text,
    supplied_routing_epoch bigint,
    supplied_engine_owner text,
    supplied_config_revision bigint,
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
    current_fence queue_v2_contract.queue_v2_write_fence%ROWTYPE;
    supplied_sequence bigint;
    inserted_count bigint;
BEGIN
    IF supplied_task_kind IS NULL OR supplied_task_kind = ''
        OR supplied_task_id IS NULL OR supplied_task_id = ''
        OR supplied_shard_id IS NULL OR supplied_shard_id = ''
        OR supplied_engine_owner IS NULL
        OR supplied_engine_owner NOT IN ('python', 'go')
        OR supplied_config_revision IS NULL
        OR supplied_config_revision NOT BETWEEN 1 AND 9999999999999
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'invalid_identity';
    END IF;
    supplied_sequence :=
        queue_v2_contract.jobseek_queue_v2_claim_sequence(
            supplied_claim_token,
            supplied_routing_epoch
        );

    BEGIN
        INSERT INTO queue_v2_contract.queue_v2_write_fence (
            task_kind,
            task_id,
            shard_id,
            routing_epoch,
            engine_owner,
            config_revision,
            claim_token,
            claim_sequence,
            state
        ) VALUES (
            supplied_task_kind,
            supplied_task_id,
            supplied_shard_id,
            supplied_routing_epoch,
            supplied_engine_owner,
            supplied_config_revision,
            supplied_claim_token,
            supplied_sequence,
            'inflight'
        )
        ON CONFLICT (task_kind, task_id) DO NOTHING;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
    EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'generation_in_use';
    END;
    IF inserted_count = 1 THEN
        RETURN;
    END IF;

    SELECT *
    INTO current_fence
    FROM queue_v2_contract.queue_v2_write_fence
    WHERE task_kind = supplied_task_kind
        AND task_id = supplied_task_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'missing';
    ELSIF current_fence.state IS DISTINCT FROM 'revoked' THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'already_active';
    ELSIF supplied_routing_epoch < current_fence.routing_epoch
        OR (
            supplied_routing_epoch = current_fence.routing_epoch
            AND supplied_sequence <= current_fence.claim_sequence
        )
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'generation_not_advanced';
    ELSIF supplied_routing_epoch = current_fence.routing_epoch
        AND (
            supplied_shard_id IS DISTINCT FROM current_fence.shard_id
            OR supplied_engine_owner IS DISTINCT FROM current_fence.engine_owner
        )
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'route_change_requires_epoch';
    END IF;

    BEGIN
        UPDATE queue_v2_contract.queue_v2_write_fence
        SET shard_id = supplied_shard_id,
            routing_epoch = supplied_routing_epoch,
            engine_owner = supplied_engine_owner,
            config_revision = supplied_config_revision,
            claim_token = supplied_claim_token,
            claim_sequence = supplied_sequence,
            state = 'inflight'
        WHERE task_kind = supplied_task_kind
            AND task_id = supplied_task_id;
    EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'generation_in_use';
    END;
END;
$$;

CREATE FUNCTION queue_v2_contract.jobseek_queue_v2_rotate_write_fence(
    supplied_task_kind text,
    supplied_task_id text,
    expected_shard_id text,
    expected_routing_epoch bigint,
    expected_engine_owner text,
    expected_config_revision bigint,
    expected_claim_token text,
    next_shard_id text,
    next_routing_epoch bigint,
    next_engine_owner text,
    next_config_revision bigint,
    next_claim_token text
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
PARALLEL UNSAFE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    current_fence queue_v2_contract.queue_v2_write_fence%ROWTYPE;
    next_sequence bigint;
BEGIN
    PERFORM queue_v2_contract.jobseek_queue_v2_require_write_fence(
        supplied_task_kind,
        supplied_task_id,
        expected_shard_id,
        expected_routing_epoch,
        expected_engine_owner,
        expected_config_revision,
        expected_claim_token
    );
    IF next_shard_id IS NULL OR next_shard_id = ''
        OR next_engine_owner IS NULL
        OR next_engine_owner NOT IN ('python', 'go')
        OR next_config_revision IS NULL
        OR next_config_revision NOT BETWEEN 1 AND 9999999999999
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'invalid_identity';
    END IF;
    next_sequence :=
        queue_v2_contract.jobseek_queue_v2_claim_sequence(
            next_claim_token,
            next_routing_epoch
        );

    SELECT *
    INTO STRICT current_fence
    FROM queue_v2_contract.queue_v2_write_fence
    WHERE task_kind = supplied_task_kind
        AND task_id = supplied_task_id;

    IF next_routing_epoch < current_fence.routing_epoch
        OR (
            next_routing_epoch = current_fence.routing_epoch
            AND next_sequence <= current_fence.claim_sequence
        )
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'generation_not_advanced';
    ELSIF next_routing_epoch = current_fence.routing_epoch
        AND (
            next_shard_id IS DISTINCT FROM current_fence.shard_id
            OR next_engine_owner IS DISTINCT FROM current_fence.engine_owner
        )
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'route_change_requires_epoch';
    END IF;

    BEGIN
        UPDATE queue_v2_contract.queue_v2_write_fence
        SET shard_id = next_shard_id,
            routing_epoch = next_routing_epoch,
            engine_owner = next_engine_owner,
            config_revision = next_config_revision,
            claim_token = next_claim_token,
            claim_sequence = next_sequence
        WHERE task_kind = supplied_task_kind
            AND task_id = supplied_task_id;
    EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'queue_v2_write_fence_rejected',
            DETAIL = 'generation_in_use';
    END;
END;
$$;

CREATE FUNCTION queue_v2_contract.jobseek_queue_v2_revoke_write_fence(
    supplied_task_kind text,
    supplied_task_id text,
    supplied_shard_id text,
    supplied_routing_epoch bigint,
    supplied_engine_owner text,
    supplied_config_revision bigint,
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
    PERFORM queue_v2_contract.jobseek_queue_v2_require_write_fence(
        supplied_task_kind,
        supplied_task_id,
        supplied_shard_id,
        supplied_routing_epoch,
        supplied_engine_owner,
        supplied_config_revision,
        supplied_claim_token
    );
    UPDATE queue_v2_contract.queue_v2_write_fence
    SET state = 'revoked'
    WHERE task_kind = supplied_task_kind
        AND task_id = supplied_task_id;
END;
$$;
