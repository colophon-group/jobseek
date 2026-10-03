-- Contract only after a verified compatible bridge release is the rollback floor.
-- Leave inactive catalogue mirror tables and saved-job snapshots untouched.
-- Bounded migration-runner lock/statement timeouts make lock contention fail
-- atomically; retry the same exact migration identity after a quiet write window.
LOCK TABLE public.company, public.company_reference, public.followed_company, public.watchlist_company IN ACCESS EXCLUSIVE MODE;
--> statement-breakpoint
DO $$
DECLARE target regclass; old_fk record; matching integer; missing bigint;
BEGIN
  SELECT count(*) INTO missing FROM (
    SELECT company_id FROM public.watchlist_company
    UNION ALL SELECT company_id FROM public.followed_company
  ) selection LEFT JOIN public.company_reference reference ON reference.id=selection.company_id
  WHERE reference.id IS NULL;
  IF missing > 0 THEN
    RAISE EXCEPTION 'Company reference contract refused: % selections lack durable references', missing;
  END IF;
  FOREACH target IN ARRAY ARRAY['public.followed_company'::regclass, 'public.watchlist_company'::regclass] LOOP
    SELECT count(*) INTO matching FROM pg_constraint
    WHERE conrelid=target AND contype='f'
      AND conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=target AND attname='company_id' AND NOT attisdropped)]::smallint[];
    IF matching <> 1 THEN
      RAISE EXCEPTION 'Company reference contract refused: unexpected selection foreign key count';
    END IF;
    SELECT * INTO old_fk FROM pg_constraint
    WHERE conrelid=target AND contype='f'
      AND conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=target AND attname='company_id' AND NOT attisdropped)]::smallint[];
    IF old_fk.confrelid <> 'public.company'::regclass
       OR old_fk.confkey <> ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid='public.company'::regclass AND attname='id' AND NOT attisdropped)]::smallint[]
       OR old_fk.confdeltype <> 'c' OR old_fk.confupdtype <> 'a'
       OR NOT old_fk.convalidated OR old_fk.condeferrable OR old_fk.condeferred THEN
      RAISE EXCEPTION 'Company reference contract refused: unexpected legacy selection foreign key definition';
    END IF;
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', target, old_fk.conname);
  END LOOP;
END $$;
--> statement-breakpoint
ALTER TABLE public.followed_company ADD CONSTRAINT followed_company_company_id_company_reference_id_fk
FOREIGN KEY (company_id) REFERENCES public.company_reference(id) ON DELETE RESTRICT;
--> statement-breakpoint
ALTER TABLE public.watchlist_company ADD CONSTRAINT watchlist_company_company_id_company_reference_id_fk
FOREIGN KEY (company_id) REFERENCES public.company_reference(id) ON DELETE RESTRICT;
--> statement-breakpoint
DROP TRIGGER company_reference_legacy_bridge ON public.company;
--> statement-breakpoint
DROP FUNCTION public.company_reference_from_legacy();
