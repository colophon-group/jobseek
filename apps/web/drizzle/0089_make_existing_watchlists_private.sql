-- Make every legacy public watchlist private while preserving a complete,
-- durable rollback inventory for the compatibility window.
--
-- This is a data migration only. Drizzle's outer transaction owns commit and
-- rollback. The migration intentionally retains watchlist.is_public and the
-- idx_wl_public partial index; removing either belongs to #8371.

DO $preflight$
DECLARE
  ledger_count integer;
  latest_hash text;
  latest_created_at bigint;
  column_count integer;
  index_definition text;
  index_valid boolean;
  index_ready boolean;
  marker_count integer;
  marker_valid boolean;
BEGIN
  SELECT count(*),
         (array_agg(hash ORDER BY created_at DESC, id DESC))[1],
         (array_agg(created_at ORDER BY created_at DESC, id DESC))[1]
  INTO ledger_count, latest_hash, latest_created_at
  FROM drizzle.__drizzle_migrations;

  IF ledger_count <> 78
     OR latest_created_at IS DISTINCT FROM 1788199156000
     OR latest_hash IS DISTINCT FROM
        'c637d066ac3d44905e9ae6cbc39f8683275a025e3f8201ae8181b3f598f94e7a'
  THEN
    RAISE EXCEPTION
      'Refusing watchlist privacy migration: expected exact 0088 ledger tip, got rows=% created_at=% hash=%',
      ledger_count,
      latest_created_at,
      latest_hash;
  END IF;

  IF to_regclass('pg_temp.jobseek_watchlist_privacy_attestation') IS NULL THEN
    RAISE EXCEPTION
      'Refusing watchlist privacy migration: session-local rollout attestation is absent';
  END IF;

  SELECT count(*), bool_and(
    confirmation = 'PRIVATE-WATCHLISTS-0089'
    AND backup_restore_run_id > 0
    AND private_mutations_deploy_sha ~ '^[0-9a-f]{40}$'
    AND route_cutover_deploy_sha ~ '^[0-9a-f]{40}$'
    AND NULLIF(btrim(route_cutover_approved_by), '') IS NOT NULL
    AND expected_public_count >= 0
    AND expected_public_digest ~ '^[0-9a-f]{32}$'
    AND attested_at >= clock_timestamp() - interval '30 minutes'
    AND attested_at <= clock_timestamp() + interval '1 minute'
  )
  INTO marker_count, marker_valid
  FROM pg_temp.jobseek_watchlist_privacy_attestation;

  IF marker_count <> 1 OR marker_valid IS DISTINCT FROM true THEN
    RAISE EXCEPTION
      'Refusing watchlist privacy migration: session-local rollout attestation is invalid';
  END IF;

  SELECT count(*)
  INTO column_count
  FROM pg_attribute
  WHERE attrelid = 'public.watchlist'::regclass
    AND attname = 'is_public'
    AND atttypid = 'boolean'::regtype
    AND attnotnull
    AND NOT attisdropped;

  IF column_count <> 1 THEN
    RAISE EXCEPTION
      'Refusing watchlist privacy migration: watchlist.is_public is not the expected NOT NULL boolean';
  END IF;

  SELECT pg_get_indexdef(indexrelid), indisvalid, indisready
  INTO index_definition, index_valid, index_ready
  FROM pg_index
  WHERE indexrelid = to_regclass('public.idx_wl_public');

  IF index_definition IS NULL
     OR index_valid IS DISTINCT FROM true
     OR index_ready IS DISTINCT FROM true
     OR index_definition NOT LIKE '% ON public.watchlist %'
     OR index_definition NOT LIKE '%(is_public)%'
     OR index_definition NOT LIKE '%WHERE (is_public = true)%'
  THEN
    RAISE EXCEPTION
      'Refusing watchlist privacy migration: idx_wl_public is absent or differs: %',
      index_definition;
  END IF;

  IF to_regclass('public.watchlist_visibility_0089_state') IS NOT NULL
     OR to_regclass('public.watchlist_visibility_0089_rollback') IS NOT NULL
  THEN
    RAISE EXCEPTION
      'Refusing watchlist privacy migration: rollback artifact relations already exist without the 0089 ledger row';
  END IF;
END
$preflight$;--> statement-breakpoint

-- Prevent watchlist, membership, and owner-path changes between the snapshot,
-- visibility update, and postconditions. The lock window contains one bounded
-- UPDATE and aggregate verification only.
LOCK TABLE public.watchlist IN SHARE ROW EXCLUSIVE MODE;--> statement-breakpoint
LOCK TABLE public.watchlist_company IN SHARE ROW EXCLUSIVE MODE;--> statement-breakpoint
LOCK TABLE public."user" IN SHARE MODE;--> statement-breakpoint

CREATE TEMPORARY TABLE jobseek_0089_watchlist_before
ON COMMIT DROP
AS
SELECT
  w.id,
  w.is_public,
  to_jsonb(w) - 'is_public' AS payload
FROM public.watchlist AS w;--> statement-breakpoint

CREATE TEMPORARY TABLE jobseek_0089_membership_before
ON COMMIT DROP
AS
SELECT wc.id, to_jsonb(wc) AS payload
FROM public.watchlist_company AS wc;--> statement-breakpoint

CREATE TABLE public.watchlist_visibility_0089_state (
  migration_key text PRIMARY KEY,
  status text NOT NULL CHECK (status IN ('private', 'rolled_back')),
  captured_at timestamp with time zone NOT NULL,
  watchlist_count bigint NOT NULL CHECK (watchlist_count >= 0),
  membership_count bigint NOT NULL CHECK (membership_count >= 0),
  public_count bigint NOT NULL CHECK (public_count >= 0),
  watchlist_content_digest text NOT NULL CHECK (watchlist_content_digest ~ '^[0-9a-f]{32}$'),
  filters_digest text NOT NULL CHECK (filters_digest ~ '^[0-9a-f]{32}$'),
  alerts_digest text NOT NULL CHECK (alerts_digest ~ '^[0-9a-f]{32}$'),
  provenance_digest text NOT NULL CHECK (provenance_digest ~ '^[0-9a-f]{32}$'),
  owners_digest text NOT NULL CHECK (owners_digest ~ '^[0-9a-f]{32}$'),
  membership_digest text NOT NULL CHECK (membership_digest ~ '^[0-9a-f]{32}$'),
  public_inventory_digest text NOT NULL CHECK (public_inventory_digest ~ '^[0-9a-f]{32}$')
);--> statement-breakpoint

CREATE TABLE public.watchlist_visibility_0089_rollback (
  watchlist_id uuid PRIMARY KEY,
  owner_user_id text NOT NULL,
  owner_name text NOT NULL,
  owner_username text,
  owner_display_username text,
  watchlist_slug text NOT NULL,
  watchlist_payload jsonb NOT NULL,
  company_memberships jsonb NOT NULL,
  path_variants jsonb NOT NULL,
  captured_at timestamp with time zone NOT NULL
);--> statement-breakpoint

INSERT INTO public.watchlist_visibility_0089_rollback (
  watchlist_id,
  owner_user_id,
  owner_name,
  owner_username,
  owner_display_username,
  watchlist_slug,
  watchlist_payload,
  company_memberships,
  path_variants,
  captured_at
)
SELECT
  w.id,
  u.id,
  u.name,
  u.username,
  u.display_username,
  w.slug,
  to_jsonb(w),
  COALESCE(
    (
      SELECT jsonb_agg(to_jsonb(wc) ORDER BY wc.id)
      FROM public.watchlist_company AS wc
      WHERE wc.watchlist_id = w.id
    ),
    '[]'::jsonb
  ),
  COALESCE(
    (
      SELECT jsonb_agg(
        jsonb_build_object(
          'locale', locale.value,
          'ownerSlugKind', owner_slug.kind,
          'ownerSlug', owner_slug.value,
          'pagePath', format('/%s/%s/%s', locale.value, owner_slug.value, w.slug),
          'ogPath', format('/og/watchlist/%s/%s/%s', locale.value, owner_slug.value, w.slug)
        )
        ORDER BY owner_slug.kind, locale.value
      )
      FROM (
        VALUES
          ('username'::text, u.username),
          ('display_username'::text, u.display_username)
      ) AS owner_slug(kind, value)
      CROSS JOIN (
        VALUES ('en'::text), ('de'::text), ('fr'::text), ('it'::text)
      ) AS locale(value)
      WHERE owner_slug.value IS NOT NULL
    ),
    '[]'::jsonb
  ),
  transaction_timestamp()
FROM public.watchlist AS w
JOIN public."user" AS u ON u.id = w.user_id
WHERE w.is_public
ORDER BY w.id;--> statement-breakpoint

INSERT INTO public.watchlist_visibility_0089_state (
  migration_key,
  status,
  captured_at,
  watchlist_count,
  membership_count,
  public_count,
  watchlist_content_digest,
  filters_digest,
  alerts_digest,
  provenance_digest,
  owners_digest,
  membership_digest,
  public_inventory_digest
)
SELECT
  '0089_make_existing_watchlists_private',
  'private',
  transaction_timestamp(),
  (SELECT count(*) FROM public.watchlist),
  (SELECT count(*) FROM public.watchlist_company),
  (SELECT count(*) FROM public.watchlist WHERE is_public),
  (
    SELECT md5(COALESCE(jsonb_agg(to_jsonb(w) - 'is_public' ORDER BY w.id)::text, '[]'))
    FROM public.watchlist AS w
  ),
  (
    SELECT md5(COALESCE(jsonb_agg(jsonb_build_object('id', w.id, 'filters', w.filters) ORDER BY w.id)::text, '[]'))
    FROM public.watchlist AS w
  ),
  (
    SELECT md5(COALESCE(jsonb_agg(jsonb_build_object('id', w.id, 'alertsEnabled', w.alerts_enabled) ORDER BY w.id)::text, '[]'))
    FROM public.watchlist AS w
  ),
  (
    SELECT md5(COALESCE(jsonb_agg(jsonb_build_object('id', w.id, 'sourceWatchlistId', w.source_watchlist_id) ORDER BY w.id)::text, '[]'))
    FROM public.watchlist AS w
  ),
  (
    SELECT md5(COALESCE(jsonb_agg(jsonb_build_object('watchlistId', w.id, 'ownerUserId', w.user_id) ORDER BY w.id)::text, '[]'))
    FROM public.watchlist AS w
  ),
  (
    SELECT md5(COALESCE(jsonb_agg(to_jsonb(wc) ORDER BY wc.id)::text, '[]'))
    FROM public.watchlist_company AS wc
  ),
  (
    SELECT md5(COALESCE(jsonb_agg(
      jsonb_build_object(
        'watchlist', rollback.watchlist_payload,
        'ownerUserId', rollback.owner_user_id,
        'ownerName', rollback.owner_name,
        'ownerUsername', rollback.owner_username,
        'ownerDisplayUsername', rollback.owner_display_username,
        'companies', rollback.company_memberships
      ) ORDER BY rollback.watchlist_id
    )::text, '[]'))
    FROM public.watchlist_visibility_0089_rollback AS rollback
  );--> statement-breakpoint

DO $inventory$
DECLARE
  expected_count bigint;
  expected_digest text;
  actual_count bigint;
  actual_digest text;
  orphaned_owner_count bigint;
BEGIN
  SELECT expected_public_count, expected_public_digest
  INTO expected_count, expected_digest
  FROM pg_temp.jobseek_watchlist_privacy_attestation;

  SELECT public_count, public_inventory_digest
  INTO actual_count, actual_digest
  FROM public.watchlist_visibility_0089_state
  WHERE migration_key = '0089_make_existing_watchlists_private';

  IF actual_count IS DISTINCT FROM expected_count
     OR actual_digest IS DISTINCT FROM expected_digest
  THEN
    RAISE EXCEPTION
      'Refusing watchlist privacy migration: live public inventory count/digest (%/%) differs from reviewed artifact (%/%)',
      actual_count,
      actual_digest,
      expected_count,
      expected_digest;
  END IF;

  SELECT count(*)
  INTO orphaned_owner_count
  FROM public.watchlist AS w
  LEFT JOIN public."user" AS u ON u.id = w.user_id
  WHERE u.id IS NULL;

  IF orphaned_owner_count <> 0 THEN
    RAISE EXCEPTION
      'Refusing watchlist privacy migration: % watchlists are not owner-readable',
      orphaned_owner_count;
  END IF;
END
$inventory$;--> statement-breakpoint

DO $migration$
DECLARE
  expected_count bigint;
  migrated_count bigint;
BEGIN
  SELECT public_count
  INTO expected_count
  FROM public.watchlist_visibility_0089_state
  WHERE migration_key = '0089_make_existing_watchlists_private';

  UPDATE public.watchlist
  SET is_public = false
  WHERE is_public = true;

  GET DIAGNOSTICS migrated_count = ROW_COUNT;

  IF migrated_count IS DISTINCT FROM expected_count THEN
    RAISE EXCEPTION
      'Watchlist privacy migration updated % rows; expected %',
      migrated_count,
      expected_count;
  END IF;
END
$migration$;--> statement-breakpoint

DO $postconditions$
DECLARE
  state_row public.watchlist_visibility_0089_state%ROWTYPE;
  current_watchlist_count bigint;
  current_membership_count bigint;
  current_public_count bigint;
  current_watchlist_content_digest text;
  current_filters_digest text;
  current_alerts_digest text;
  current_provenance_digest text;
  current_owners_digest text;
  current_membership_digest text;
  orphaned_owner_count bigint;
  index_definition text;
  index_valid boolean;
  index_ready boolean;
BEGIN
  SELECT *
  INTO STRICT state_row
  FROM public.watchlist_visibility_0089_state
  WHERE migration_key = '0089_make_existing_watchlists_private';

  SELECT
    count(*),
    count(*) FILTER (WHERE is_public),
    md5(COALESCE(jsonb_agg(to_jsonb(w) - 'is_public' ORDER BY w.id)::text, '[]')),
    md5(COALESCE(jsonb_agg(jsonb_build_object('id', w.id, 'filters', w.filters) ORDER BY w.id)::text, '[]')),
    md5(COALESCE(jsonb_agg(jsonb_build_object('id', w.id, 'alertsEnabled', w.alerts_enabled) ORDER BY w.id)::text, '[]')),
    md5(COALESCE(jsonb_agg(jsonb_build_object('id', w.id, 'sourceWatchlistId', w.source_watchlist_id) ORDER BY w.id)::text, '[]')),
    md5(COALESCE(jsonb_agg(jsonb_build_object('watchlistId', w.id, 'ownerUserId', w.user_id) ORDER BY w.id)::text, '[]'))
  INTO
    current_watchlist_count,
    current_public_count,
    current_watchlist_content_digest,
    current_filters_digest,
    current_alerts_digest,
    current_provenance_digest,
    current_owners_digest
  FROM public.watchlist AS w;

  SELECT
    count(*),
    md5(COALESCE(jsonb_agg(to_jsonb(wc) ORDER BY wc.id)::text, '[]'))
  INTO current_membership_count, current_membership_digest
  FROM public.watchlist_company AS wc;

  SELECT count(*)
  INTO orphaned_owner_count
  FROM public.watchlist AS w
  LEFT JOIN public."user" AS u ON u.id = w.user_id
  WHERE u.id IS NULL;

  IF current_watchlist_count IS DISTINCT FROM state_row.watchlist_count
     OR current_membership_count IS DISTINCT FROM state_row.membership_count
     OR current_public_count <> 0
     OR current_watchlist_content_digest IS DISTINCT FROM state_row.watchlist_content_digest
     OR current_filters_digest IS DISTINCT FROM state_row.filters_digest
     OR current_alerts_digest IS DISTINCT FROM state_row.alerts_digest
     OR current_provenance_digest IS DISTINCT FROM state_row.provenance_digest
     OR current_owners_digest IS DISTINCT FROM state_row.owners_digest
     OR current_membership_digest IS DISTINCT FROM state_row.membership_digest
     OR orphaned_owner_count <> 0
  THEN
    RAISE EXCEPTION
      'Watchlist privacy migration changed protected watchlist, filter, alert, provenance, owner, or membership state';
  END IF;

  IF EXISTS (
    (SELECT id, payload FROM jobseek_0089_watchlist_before
     EXCEPT
     SELECT w.id, to_jsonb(w) - 'is_public' FROM public.watchlist AS w)
    UNION ALL
    (SELECT w.id, to_jsonb(w) - 'is_public' FROM public.watchlist AS w
     EXCEPT
     SELECT id, payload FROM jobseek_0089_watchlist_before)
  ) THEN
    RAISE EXCEPTION
      'Watchlist privacy migration changed watchlist row content other than is_public';
  END IF;

  IF EXISTS (
    (SELECT id, payload FROM jobseek_0089_membership_before
     EXCEPT
     SELECT wc.id, to_jsonb(wc) FROM public.watchlist_company AS wc)
    UNION ALL
    (SELECT wc.id, to_jsonb(wc) FROM public.watchlist_company AS wc
     EXCEPT
     SELECT id, payload FROM jobseek_0089_membership_before)
  ) THEN
    RAISE EXCEPTION
      'Watchlist privacy migration changed watchlist_company content';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.watchlist_visibility_0089_rollback AS rollback
    LEFT JOIN public.watchlist AS w ON w.id = rollback.watchlist_id
    WHERE w.id IS NULL
       OR w.user_id IS DISTINCT FROM rollback.owner_user_id
       OR w.source_watchlist_id IS DISTINCT FROM
          (rollback.watchlist_payload ->> 'source_watchlist_id')::uuid
  ) THEN
    RAISE EXCEPTION
      'Watchlist privacy migration lost an owner, copy, or source provenance row';
  END IF;

  SELECT pg_get_indexdef(indexrelid), indisvalid, indisready
  INTO index_definition, index_valid, index_ready
  FROM pg_index
  WHERE indexrelid = to_regclass('public.idx_wl_public');

  IF index_definition IS NULL
     OR index_valid IS DISTINCT FROM true
     OR index_ready IS DISTINCT FROM true
     OR index_definition NOT LIKE '%WHERE (is_public = true)%'
  THEN
    RAISE EXCEPTION
      'Watchlist privacy migration did not retain valid idx_wl_public';
  END IF;
END
$postconditions$;
