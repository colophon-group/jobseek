-- Restored authority at the already reserved R. No allocator adoption or R+1.
CREATE TABLE public.crawler_ownership_restoration_finalization (
 plan_sha256 text PRIMARY KEY CHECK(plan_sha256 ~ '^[0-9a-f]{64}$'),
 ordinary_restoration_plan_sha256 text NOT NULL UNIQUE REFERENCES public.crawler_ownership_ordinary_restoration(plan_sha256),
 b0_reactivation_plan_sha256 text NOT NULL REFERENCES public.crawler_ownership_b0_reactivation(plan_sha256),
 b0_receipt_sha256 text NOT NULL REFERENCES public.crawler_ownership_b0_reactivation_completion(receipt_sha256),
 source_revision text NOT NULL CHECK(source_revision ~ '^[0-9a-f]{40}$'),
 retirement_epoch bigint NOT NULL CHECK(retirement_epoch BETWEEN 2 AND 9999999999999),
 reversal_sha256 text NOT NULL UNIQUE REFERENCES public.crawler_ownership_reversal(reversal_sha256),
 target_sha256 text NOT NULL REFERENCES public.crawler_ownership_b0_target(target_sha256),
 fresh_ordinary_plan_sha256 text REFERENCES public.ordinary_worker_ownership_plan(plan_sha256),
 compatible_intent_sha256 text UNIQUE CHECK(compatible_intent_sha256 ~ '^[0-9a-f]{64}$'),
 payload text NOT NULL CHECK(octet_length(payload) BETWEEN 1 AND 33554432),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(plan_sha256=encode(sha256(convert_to(payload,'UTF8')),'hex')),
 CHECK((payload::jsonb->>'version'='jobseek.crawler.cold-ordinary-finalization/v1') IS TRUE),
 CHECK((payload::jsonb->'request'->>'ordinary_restoration_plan_sha256'=ordinary_restoration_plan_sha256) IS TRUE),
 CHECK((payload::jsonb->'request'->>'b0_reactivation_plan_sha256'=b0_reactivation_plan_sha256) IS TRUE),
 CHECK((payload::jsonb->'request'->>'source_revision'=source_revision) IS TRUE),
 CHECK((payload::jsonb->>'retirement_epoch'=retirement_epoch::text) IS TRUE),
 CHECK((payload::jsonb->>'reversal_sha256'=reversal_sha256) IS TRUE),
 CHECK((payload::jsonb->>'target_sha256'=target_sha256) IS TRUE),
 CHECK((payload::jsonb->>'b0_receipt_sha256'=b0_receipt_sha256) IS TRUE),
 CHECK((payload::jsonb->>'fresh_ordinary_plan_sha256'=COALESCE(fresh_ordinary_plan_sha256,'')) IS TRUE),
 CHECK((payload::jsonb->>'compatible_intent_sha256'=COALESCE(compatible_intent_sha256,'')) IS TRUE),
 CHECK((fresh_ordinary_plan_sha256 IS NULL)=(compatible_intent_sha256 IS NULL))
);

CREATE FUNCTION public.jobseek_crawler_restoration_cold_context(p public.crawler_ownership_restoration_finalization)
RETURNS void LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE ordinary public.crawler_ownership_ordinary_restoration%ROWTYPE;
 b0 public.crawler_ownership_b0_reactivation%ROWTYPE;
 reversal public.crawler_ownership_reversal%ROWTYPE;
 forward public.crawler_ownership_transition%ROWTYPE;
 spec jsonb; expected jsonb; current_epoch bigint; allocator_called boolean;
BEGIN
 SELECT * INTO ordinary FROM public.crawler_ownership_ordinary_restoration WHERE plan_sha256=p.ordinary_restoration_plan_sha256;
 SELECT * INTO b0 FROM public.crawler_ownership_b0_reactivation WHERE plan_sha256=p.b0_reactivation_plan_sha256;
 IF ordinary.source_revision IS DISTINCT FROM p.source_revision OR ordinary.reversal_sha256 IS DISTINCT FROM p.reversal_sha256
  OR ordinary.retirement_epoch IS DISTINCT FROM p.retirement_epoch
  OR ordinary.fresh_ordinary_plan_sha256 IS DISTINCT FROM p.fresh_ordinary_plan_sha256
  OR b0.ordinary_restoration_plan_sha256 IS DISTINCT FROM ordinary.plan_sha256
  OR b0.source_revision IS DISTINCT FROM p.source_revision OR b0.retirement_epoch IS DISTINCT FROM p.retirement_epoch
  OR b0.target_sha256 IS DISTINCT FROM p.target_sha256
  OR NOT EXISTS(SELECT 1 FROM public.crawler_ownership_b0_reactivation_completion WHERE plan_sha256=b0.plan_sha256 AND source_revision=p.source_revision AND receipt_sha256=p.b0_receipt_sha256)
  OR (p.payload::jsonb->>'previous_projection') IS DISTINCT FROM (b0.payload::jsonb->>'previous_projection')
  OR (p.payload::jsonb->>'previous_marker') IS DISTINCT FROM (b0.payload::jsonb->>'previous_marker')
  OR (p.payload::jsonb->>'mode') IS DISTINCT FROM (ordinary.payload::jsonb->>'mode')
 THEN RAISE EXCEPTION 'crawler_ownership_restoration_approval_binding_rejected'; END IF;
 SELECT * INTO reversal FROM public.crawler_ownership_reversal WHERE reversal_sha256=p.reversal_sha256;
 SELECT * INTO forward FROM public.crawler_ownership_transition WHERE intent_sha256=reversal.forward_intent_sha256;
 IF reversal.phase IS DISTINCT FROM 'reserved' OR reversal.source_revision IS DISTINCT FROM p.source_revision
  OR reversal.retirement_epoch IS DISTINCT FROM p.retirement_epoch OR forward.phase IS DISTINCT FROM 'reversing'
  OR forward.source_revision IS DISTINCT FROM p.source_revision OR forward.routing_epoch IS DISTINCT FROM reversal.source_epoch
  OR forward.reserved_plan_sha256 IS DISTINCT FROM reversal.source_plan_sha256
  OR NOT EXISTS(SELECT 1 FROM public.crawler_ownership_b0_restoration WHERE plan_sha256=ordinary.b0_restoration_plan_sha256 AND phase='fences-cleared' AND reversal_sha256=p.reversal_sha256 AND source_revision=p.source_revision AND retirement_epoch=p.retirement_epoch)
  OR (p.payload::jsonb->'request'->>'active_release_sha256') IS NULL
  OR (p.payload::jsonb->'request'->>'active_release_sha256') NOT IN (forward.payload::jsonb->>'active_release_sha256',forward.payload::jsonb->>'target_release_sha256')
  OR COALESCE(p.payload::jsonb->'request'->>'transition_id','') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  OR COALESCE(p.payload::jsonb->'request'->>'cold_attestation_sha256','') !~ '^[0-9a-f]{64}$'
 THEN RAISE EXCEPTION 'crawler_ownership_restoration_context_rejected'; END IF;
 IF p.fresh_ordinary_plan_sha256 IS NULL THEN
  IF (p.payload::jsonb->>'mode') IS DISTINCT FROM 'legacy' OR (p.payload::jsonb->>'compatible_payload') IS DISTINCT FROM ''
  THEN RAISE EXCEPTION 'crawler_ownership_restoration_legacy_rejected'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE plan_sha256=p.fresh_ordinary_plan_sha256 AND state='staged' AND routing_epoch=p.retirement_epoch AND source_revision=ordinary.payload::jsonb->'request'->>'rollback_source_revision')
  THEN RAISE EXCEPTION 'crawler_ownership_restoration_fresh_owner_rejected'; END IF;
  spec := (p.payload::jsonb->>'compatible_payload')::jsonb;
  expected := jsonb_build_object(
   'version','jobseek.crawler.cold-transition/v1','transition_id',p.payload::jsonb->'request'->>'transition_id',
   'source_revision',ordinary.payload::jsonb->'request'->>'rollback_source_revision',
   'previous_epoch',ordinary.payload::jsonb->'previous_owner'->'routing_epoch',
   'previous_ordinary_plan_sha256',ordinary.payload::jsonb->>'previous_ordinary_plan_sha256',
   'prepared_plan_sha256',ordinary.payload::jsonb->>'previous_ordinary_plan_sha256',
   'previous_b0_receipt_sha256',reversal.payload::jsonb->>'rollback_b0_receipt_sha256',
   'target_b0_manifest_sha256',p.target_sha256,'active_release_sha256',p.payload::jsonb->'request'->>'active_release_sha256',
   'target_release_sha256',reversal.payload::jsonb->>'rollback_release_sha256',
   'rollback_release_sha256',reversal.payload::jsonb->>'rollback_release_sha256',
   'cold_attestation_sha256',p.payload::jsonb->'request'->>'cold_attestation_sha256');
  IF spec IS DISTINCT FROM expected OR p.compatible_intent_sha256 IS DISTINCT FROM encode(sha256(convert_to(p.payload::jsonb->>'compatible_payload','UTF8')),'hex')
   OR EXISTS(SELECT 1 FROM public.crawler_ownership_transition WHERE transition_id=(spec->>'transition_id')::uuid)
  THEN RAISE EXCEPTION 'crawler_ownership_restoration_compatible_spec_rejected'; END IF;
 END IF;
 SELECT last_value,is_called INTO current_epoch,allocator_called FROM public.lightpanda_b0_routing_epoch_seq;
 IF allocator_called IS DISTINCT FROM true OR current_epoch IS DISTINCT FROM p.retirement_epoch
  OR EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active')
 THEN RAISE EXCEPTION 'crawler_ownership_restoration_epoch_rejected'; END IF;
END;
$$;
CREATE FUNCTION public.jobseek_crawler_restoration_finalization()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(7544422533504811010);
 PERFORM pg_advisory_xact_lock(7544422533504811009);
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF;
 PERFORM public.jobseek_crawler_restoration_cold_context(NEW);
 RETURN NEW;
END;
$$;
CREATE TRIGGER crawler_ownership_restoration_finalization BEFORE INSERT OR UPDATE OR DELETE
ON public.crawler_ownership_restoration_finalization FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_restoration_finalization();

CREATE TABLE public.crawler_ownership_restoration_publication (
 plan_sha256 text PRIMARY KEY REFERENCES public.crawler_ownership_restoration_finalization(plan_sha256),
 source_revision text NOT NULL CHECK(source_revision ~ '^[0-9a-f]{40}$'),
 phase text NOT NULL DEFAULT 'publishing' CHECK(phase IN ('publishing','published')),
 receipt_sha256 text UNIQUE CHECK(receipt_sha256 ~ '^[0-9a-f]{64}$'),
 payload text CHECK(octet_length(payload) BETWEEN 1 AND 4096),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((phase='publishing' AND receipt_sha256 IS NULL AND payload IS NULL)
  OR (phase='published' AND receipt_sha256 IS NOT NULL AND payload IS NOT NULL)),
 CHECK(receipt_sha256=encode(sha256(convert_to(payload,'UTF8')),'hex'))
);
CREATE FUNCTION public.jobseek_crawler_restoration_publication()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE p public.crawler_ownership_restoration_finalization%ROWTYPE;
 ordinary public.crawler_ownership_ordinary_restoration%ROWTYPE;
 marker text; spec jsonb; expected jsonb; projection_sha text;
BEGIN
 PERFORM pg_advisory_xact_lock(7544422533504811010);
 PERFORM pg_advisory_xact_lock(7544422533504811009);
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF;
 SELECT * INTO p FROM public.crawler_ownership_restoration_finalization WHERE plan_sha256=NEW.plan_sha256;
 IF NOT FOUND OR p.source_revision<>NEW.source_revision THEN RAISE EXCEPTION 'crawler_ownership_restoration_publication_binding_rejected'; END IF;
 PERFORM public.jobseek_crawler_restoration_cold_context(p);
 IF TG_OP='INSERT' THEN
  IF NEW.phase<>'publishing' THEN RAISE EXCEPTION 'crawler_ownership_restoration_publication_pending_required'; END IF;
 ELSE
  IF NEW.plan_sha256 IS DISTINCT FROM OLD.plan_sha256 OR NEW.source_revision IS DISTINCT FROM OLD.source_revision
   OR NEW.created_at IS DISTINCT FROM OLD.created_at OR OLD.phase<>'publishing' OR NEW.phase<>'published'
  THEN RAISE EXCEPTION 'crawler_ownership_restoration_publication_transition_rejected'; END IF;
 END IF;
 IF NEW.phase='published' THEN
  IF p.fresh_ordinary_plan_sha256 IS NULL THEN
   marker := format('{"version":"jobseek.crawler.cold-ordinary-legacy-publication/v1","state":"published","plan_sha256":"%s","retirement_epoch":%s}',p.plan_sha256,p.retirement_epoch);
   projection_sha := encode(sha256(convert_to('','UTF8')),'hex');
  ELSE
   SELECT * INTO ordinary FROM public.crawler_ownership_ordinary_restoration WHERE plan_sha256=p.ordinary_restoration_plan_sha256;
   spec := (p.payload::jsonb->>'compatible_payload')::jsonb;
   marker := format('{"version":"jobseek.crawler.cold-publication/v1","state":"published","intent_sha256":"%s","source_revision":"%s","routing_epoch":%s,"plan_sha256":"%s","b0_target_sha256":"%s"}',p.compatible_intent_sha256,spec->>'source_revision',p.retirement_epoch,p.fresh_ordinary_plan_sha256,p.target_sha256);
   projection_sha := p.fresh_ordinary_plan_sha256;
  END IF;
  expected := jsonb_build_object('version','jobseek.crawler.cold-ordinary-publication/v1','plan_sha256',p.plan_sha256,
   'source_revision',p.source_revision,'retirement_epoch',p.retirement_epoch,'b0_receipt_sha256',p.b0_receipt_sha256,
   'projection_sha256',projection_sha,'marker_sha256',encode(sha256(convert_to(marker,'UTF8')),'hex'),
   'compatible_intent_sha256',COALESCE(p.compatible_intent_sha256,''));
  IF NEW.payload::jsonb IS DISTINCT FROM expected THEN RAISE EXCEPTION 'crawler_ownership_restoration_publication_receipt_rejected'; END IF;
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER crawler_ownership_restoration_publication BEFORE INSERT OR UPDATE OR DELETE
ON public.crawler_ownership_restoration_publication FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_restoration_publication();

CREATE TABLE public.crawler_ownership_restoration_completion (
 plan_sha256 text PRIMARY KEY REFERENCES public.crawler_ownership_restoration_finalization(plan_sha256),
 source_revision text NOT NULL CHECK(source_revision ~ '^[0-9a-f]{40}$'),
 receipt_sha256 text NOT NULL UNIQUE CHECK(receipt_sha256 ~ '^[0-9a-f]{64}$'),
 payload text NOT NULL CHECK(octet_length(payload) BETWEEN 1 AND 4096),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(receipt_sha256=encode(sha256(convert_to(payload,'UTF8')),'hex'))
);
CREATE FUNCTION public.jobseek_crawler_restoration_completion()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE p public.crawler_ownership_restoration_finalization%ROWTYPE;
 publication public.crawler_ownership_restoration_publication%ROWTYPE;
 reversal public.crawler_ownership_reversal%ROWTYPE;
 current_epoch bigint; allocator_called boolean; expected jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(7544422533504811010);
 PERFORM pg_advisory_xact_lock(7544422533504811009);
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF;
 SELECT * INTO p FROM public.crawler_ownership_restoration_finalization WHERE plan_sha256=NEW.plan_sha256;
 IF NOT FOUND OR p.source_revision<>NEW.source_revision THEN RAISE EXCEPTION 'crawler_ownership_restoration_completion_binding_rejected'; END IF;
 SELECT * INTO publication FROM public.crawler_ownership_restoration_publication WHERE plan_sha256=p.plan_sha256;
 SELECT * INTO reversal FROM public.crawler_ownership_reversal WHERE reversal_sha256=p.reversal_sha256;
 IF publication.phase IS DISTINCT FROM 'published' OR reversal.phase IS DISTINCT FROM 'complete'
  OR reversal.retirement_epoch IS DISTINCT FROM p.retirement_epoch
  OR NOT EXISTS(SELECT 1 FROM public.crawler_ownership_transition WHERE intent_sha256=reversal.forward_intent_sha256 AND phase='reversed')
 THEN RAISE EXCEPTION 'crawler_ownership_restoration_completion_context_rejected'; END IF;
 IF p.fresh_ordinary_plan_sha256 IS NULL THEN
  IF EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active')
   OR EXISTS(SELECT 1 FROM public.crawler_ownership_transition WHERE phase NOT IN ('reversed','superseded'))
  THEN RAISE EXCEPTION 'crawler_ownership_restoration_completion_legacy_rejected'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE plan_sha256=p.fresh_ordinary_plan_sha256 AND state='active' AND routing_epoch=p.retirement_epoch)
   OR NOT EXISTS(SELECT 1 FROM public.crawler_ownership_transition WHERE intent_sha256=p.compatible_intent_sha256 AND phase='active' AND routing_epoch=p.retirement_epoch AND reserved_plan_sha256=p.fresh_ordinary_plan_sha256 AND payload=p.payload::jsonb->>'compatible_payload')
  THEN RAISE EXCEPTION 'crawler_ownership_restoration_completion_native_rejected'; END IF;
 END IF;
 SELECT last_value,is_called INTO current_epoch,allocator_called FROM public.lightpanda_b0_routing_epoch_seq;
 IF allocator_called IS DISTINCT FROM true OR current_epoch IS DISTINCT FROM p.retirement_epoch THEN RAISE EXCEPTION 'crawler_ownership_restoration_epoch_rejected'; END IF;
 expected := jsonb_build_object('version','jobseek.crawler.cold-ordinary-completion/v1','plan_sha256',p.plan_sha256,
  'source_revision',p.source_revision,'retirement_epoch',p.retirement_epoch,'publication_receipt_sha256',publication.receipt_sha256,
  'b0_receipt_sha256',p.b0_receipt_sha256,'reversal_sha256',p.reversal_sha256,
  'fresh_ordinary_plan_sha256',COALESCE(p.fresh_ordinary_plan_sha256,''),'compatible_intent_sha256',COALESCE(p.compatible_intent_sha256,''));
 IF NEW.payload::jsonb IS DISTINCT FROM expected THEN RAISE EXCEPTION 'crawler_ownership_restoration_completion_receipt_rejected'; END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER crawler_ownership_restoration_completion BEFORE INSERT OR UPDATE OR DELETE
ON public.crawler_ownership_restoration_completion FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_restoration_completion();

-- Complete is durable restoration, not reservation. Retain existing history.
ALTER TABLE public.crawler_ownership_reversal DROP CONSTRAINT crawler_ownership_reversal_phase_check;
ALTER TABLE public.crawler_ownership_reversal ADD CONSTRAINT crawler_ownership_reversal_phase_check CHECK(phase IN ('pending','reserved','complete'));
DO $$ DECLARE c text; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='public.crawler_ownership_reversal'::regclass AND contype='c'
  AND pg_get_constraintdef(oid) LIKE '%phase%' AND pg_get_constraintdef(oid) LIKE '%retirement_epoch%'
 LOOP EXECUTE format('ALTER TABLE public.crawler_ownership_reversal DROP CONSTRAINT %I',c); END LOOP;
END $$;
ALTER TABLE public.crawler_ownership_reversal ADD CONSTRAINT crawler_ownership_reversal_retirement_phase CHECK((phase='pending' AND retirement_epoch IS NULL) OR (phase IN ('reserved','complete') AND retirement_epoch IS NOT NULL));

CREATE OR REPLACE FUNCTION public.jobseek_crawler_ownership_reversal_transition()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE forward public.crawler_ownership_transition%ROWTYPE;
    current_epoch bigint; allocator_called boolean;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811010);
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811009);
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF;
    SELECT * INTO forward FROM public.crawler_ownership_transition
    WHERE intent_sha256=NEW.forward_intent_sha256;
    IF NOT FOUND OR forward.source_revision IS DISTINCT FROM NEW.source_revision
        OR forward.routing_epoch IS DISTINCT FROM NEW.source_epoch
        OR forward.reserved_plan_sha256 IS DISTINCT FROM NEW.source_plan_sha256
        OR (forward.payload::jsonb->>'rollback_release_sha256') IS DISTINCT FROM
            (NEW.payload::jsonb->>'rollback_release_sha256')
        OR (forward.payload::jsonb->>'previous_ordinary_plan_sha256') IS DISTINCT FROM
            (NEW.payload::jsonb->>'rollback_ordinary_plan_sha256')
        OR (forward.payload::jsonb->>'previous_b0_receipt_sha256') IS DISTINCT FROM
            (NEW.payload::jsonb->>'rollback_b0_receipt_sha256')
    THEN RAISE EXCEPTION 'crawler_ownership_reversal_binding_rejected'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.phase <> 'pending' OR forward.phase IS DISTINCT FROM NEW.source_phase
        THEN RAISE EXCEPTION 'crawler_ownership_reversal_pending_required'; END IF;
    ELSE
        IF NEW.reversal_sha256 IS DISTINCT FROM OLD.reversal_sha256
            OR NEW.reversal_id IS DISTINCT FROM OLD.reversal_id
            OR NEW.forward_intent_sha256 IS DISTINCT FROM OLD.forward_intent_sha256
            OR NEW.source_revision IS DISTINCT FROM OLD.source_revision
            OR NEW.source_epoch IS DISTINCT FROM OLD.source_epoch
            OR NEW.source_plan_sha256 IS DISTINCT FROM OLD.source_plan_sha256
            OR NEW.source_phase IS DISTINCT FROM OLD.source_phase
            OR NEW.payload IS DISTINCT FROM OLD.payload
            OR NEW.created_at IS DISTINCT FROM OLD.created_at
            OR NOT ((OLD.phase='pending' AND NEW.phase='reserved') OR (OLD.phase='reserved' AND NEW.phase='complete'))
            OR forward.phase <> 'reversing'
        THEN RAISE EXCEPTION 'crawler_ownership_reversal_rejected'; END IF;
    END IF;
    SELECT last_value,is_called INTO current_epoch,allocator_called
    FROM public.lightpanda_b0_routing_epoch_seq;
    IF allocator_called IS DISTINCT FROM true
        OR (TG_OP='INSERT' AND current_epoch IS DISTINCT FROM NEW.source_epoch)
        OR (NEW.phase IN ('reserved','complete') AND current_epoch IS DISTINCT FROM NEW.retirement_epoch)
    THEN RAISE EXCEPTION 'crawler_ownership_reversal_epoch_rejected'; END IF;
    IF NEW.phase IN ('reserved','complete') AND EXISTS (
        SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active')
    THEN RAISE EXCEPTION 'crawler_ownership_reversal_owner_rejected'; END IF;
    IF NEW.phase='complete' AND NOT EXISTS (
        SELECT 1 FROM public.crawler_ownership_restoration_finalization p
        JOIN public.crawler_ownership_restoration_publication pub ON pub.plan_sha256=p.plan_sha256
        WHERE p.reversal_sha256=NEW.reversal_sha256 AND p.source_revision=NEW.source_revision
         AND p.retirement_epoch=NEW.retirement_epoch AND pub.phase='published')
    THEN RAISE EXCEPTION 'crawler_ownership_restoration_publication_required'; END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.jobseek_crawler_ownership_journal_transition()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
DECLARE current_epoch bigint; allocator_called boolean;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811010);
    PERFORM pg_catalog.pg_advisory_xact_lock(7544422533504811009);
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'crawler_ownership_history_retained'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.phase='active' THEN
            IF NOT EXISTS (
             SELECT 1 FROM public.crawler_ownership_restoration_finalization p
             JOIN public.crawler_ownership_restoration_publication pub ON pub.plan_sha256=p.plan_sha256
             JOIN public.crawler_ownership_reversal r ON r.reversal_sha256=p.reversal_sha256
             JOIN public.crawler_ownership_transition restored_forward ON restored_forward.intent_sha256=r.forward_intent_sha256
             WHERE p.compatible_intent_sha256=NEW.intent_sha256 AND p.fresh_ordinary_plan_sha256=NEW.reserved_plan_sha256
              AND p.retirement_epoch=NEW.routing_epoch AND pub.phase='published' AND r.phase='complete' AND restored_forward.phase='reversed'
              AND NEW.payload=p.payload::jsonb->>'compatible_payload'
              AND NEW.source_revision=(p.payload::jsonb->>'compatible_payload')::jsonb->>'source_revision'
              AND NEW.previous_epoch=((p.payload::jsonb->>'compatible_payload')::jsonb->>'previous_epoch')::bigint
              AND NEW.prepared_plan_sha256=(p.payload::jsonb->>'compatible_payload')::jsonb->>'prepared_plan_sha256'
              AND NEW.transition_id=((p.payload::jsonb->>'compatible_payload')::jsonb->>'transition_id')::uuid)
            THEN RAISE EXCEPTION 'crawler_ownership_restoration_active_journal_rejected'; END IF;
        ELSIF NEW.phase <> 'pending' THEN RAISE EXCEPTION 'crawler_ownership_pending_required'; END IF;
    ELSE
        IF NEW.intent_sha256 IS DISTINCT FROM OLD.intent_sha256
            OR NEW.transition_id IS DISTINCT FROM OLD.transition_id
            OR NEW.source_revision IS DISTINCT FROM OLD.source_revision
            OR NEW.previous_epoch IS DISTINCT FROM OLD.previous_epoch
            OR NEW.prepared_plan_sha256 IS DISTINCT FROM OLD.prepared_plan_sha256
            OR NEW.payload IS DISTINCT FROM OLD.payload
            OR NEW.created_at IS DISTINCT FROM OLD.created_at
            OR (OLD.phase <> 'pending' AND (
                NEW.routing_epoch IS DISTINCT FROM OLD.routing_epoch
                OR NEW.reserved_plan_sha256 IS DISTINCT FROM OLD.reserved_plan_sha256))
            OR NOT ((OLD.phase='pending' AND NEW.phase='reserved')
                OR (OLD.phase='reserved' AND NEW.phase='publishing')
                OR (OLD.phase='publishing' AND NEW.phase='published')
                OR (OLD.phase='published' AND NEW.phase='active')
                OR (OLD.phase IN ('reserved','publishing','published','active') AND NEW.phase='reversing')
                OR (OLD.phase='reversing' AND NEW.phase='reversed')
                OR (OLD.phase='active' AND NEW.phase='superseded'))
        THEN RAISE EXCEPTION 'crawler_ownership_journal_rejected'; END IF;
    END IF;
    IF TG_OP='INSERT' OR NEW.phase IN ('reserved','publishing','published','active') THEN
        SELECT last_value,is_called INTO current_epoch,allocator_called
        FROM public.lightpanda_b0_routing_epoch_seq;
        IF allocator_called IS DISTINCT FROM true
            OR (TG_OP='INSERT' AND NEW.phase='pending' AND current_epoch IS DISTINCT FROM NEW.previous_epoch)
            OR (TG_OP='INSERT' AND NEW.phase='active' AND current_epoch IS DISTINCT FROM NEW.routing_epoch)
            OR (TG_OP<>'INSERT' AND current_epoch IS DISTINCT FROM NEW.routing_epoch)
        THEN RAISE EXCEPTION 'crawler_ownership_journal_epoch_rejected'; END IF;
    END IF;
    IF NEW.phase IN ('reserved','publishing','published','active') AND NOT EXISTS (
        SELECT 1 FROM public.ordinary_worker_ownership_plan
        WHERE plan_sha256=NEW.reserved_plan_sha256 AND source_revision=NEW.source_revision
          AND routing_epoch=NEW.routing_epoch
          AND state=CASE WHEN NEW.phase='active' THEN 'active' ELSE 'staged' END)
    THEN RAISE EXCEPTION 'crawler_ownership_reserved_plan_rejected'; END IF;
    IF NEW.phase IN ('publishing','published','active') AND NOT EXISTS (SELECT 1 FROM public.crawler_ownership_b0_target WHERE target_sha256=NEW.payload::jsonb->>'target_b0_manifest_sha256') THEN RAISE EXCEPTION 'crawler_ownership_target_required'; END IF;
    IF NEW.phase='reversing' AND NOT EXISTS (SELECT 1 FROM public.crawler_ownership_reversal WHERE forward_intent_sha256=NEW.intent_sha256 AND source_revision=NEW.source_revision AND source_epoch=NEW.routing_epoch AND source_plan_sha256=NEW.reserved_plan_sha256 AND phase IN ('pending','reserved')) THEN RAISE EXCEPTION 'crawler_ownership_reversal_required'; END IF;
    -- Retirement is not restoration. A later verified restoration protocol must
    -- add its complete state before any reversal can release ordinary claims.
    IF NEW.phase='reversed' AND NOT EXISTS (SELECT 1 FROM public.crawler_ownership_reversal WHERE forward_intent_sha256=NEW.intent_sha256 AND phase='complete') THEN RAISE EXCEPTION 'crawler_ownership_reversal_incomplete'; END IF;
    RETURN NEW;
END;
$$;

-- Deferred correlation makes the final authority changes inseparable at commit.
-- No session flag, disabled trigger or broader active-journal exception exists.
CREATE FUNCTION public.jobseek_crawler_restoration_integrity()
RETURNS trigger LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
SET search_path=pg_catalog AS $$
BEGIN
 IF TG_TABLE_NAME='crawler_ownership_reversal' THEN
  IF NEW.phase='complete' AND NOT EXISTS(SELECT 1 FROM public.crawler_ownership_restoration_finalization p JOIN public.crawler_ownership_restoration_completion c ON c.plan_sha256=p.plan_sha256 WHERE p.reversal_sha256=NEW.reversal_sha256)
  THEN RAISE EXCEPTION 'crawler_ownership_restoration_completion_required'; END IF;
 ELSIF TG_TABLE_NAME='crawler_ownership_transition' THEN
  IF NEW.phase='reversed' AND NOT EXISTS(SELECT 1 FROM public.crawler_ownership_restoration_finalization p JOIN public.crawler_ownership_restoration_completion c ON c.plan_sha256=p.plan_sha256 JOIN public.crawler_ownership_reversal r ON r.reversal_sha256=p.reversal_sha256 WHERE r.forward_intent_sha256=NEW.intent_sha256)
  THEN RAISE EXCEPTION 'crawler_ownership_restoration_completion_required'; END IF;
 ELSE
  IF NEW.state='active' AND EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE plan_sha256=NEW.plan_sha256 AND state='active') THEN
   IF EXISTS(SELECT 1 FROM public.crawler_ownership_transition WHERE phase NOT IN ('reversed','superseded') AND (phase<>'active' OR reserved_plan_sha256<>NEW.plan_sha256))
    OR (EXISTS(SELECT 1 FROM public.crawler_ownership_ordinary_restoration WHERE fresh_ordinary_plan_sha256=NEW.plan_sha256)
     AND NOT EXISTS(SELECT 1 FROM public.crawler_ownership_restoration_finalization p JOIN public.crawler_ownership_restoration_completion c ON c.plan_sha256=p.plan_sha256 WHERE p.fresh_ordinary_plan_sha256=NEW.plan_sha256))
   THEN RAISE EXCEPTION 'crawler_ownership_restoration_owner_completion_required'; END IF;
  END IF;
 END IF;
 RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER crawler_restoration_reversal_integrity AFTER INSERT OR UPDATE ON public.crawler_ownership_reversal
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_restoration_integrity();
CREATE CONSTRAINT TRIGGER crawler_restoration_journal_integrity AFTER INSERT OR UPDATE ON public.crawler_ownership_transition
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_restoration_integrity();
CREATE CONSTRAINT TRIGGER crawler_restoration_owner_integrity AFTER INSERT OR UPDATE ON public.ordinary_worker_ownership_plan
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.jobseek_crawler_restoration_integrity();
