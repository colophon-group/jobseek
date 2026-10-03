-- Expand only: do not mutate selections or change their legacy foreign keys.
-- Hold legacy writers until the seed and its compatibility trigger are installed.
LOCK TABLE public.company IN SHARE ROW EXCLUSIVE MODE;
--> statement-breakpoint
DO $$
DECLARE malformed bigint;
BEGIN
  SELECT count(*) INTO malformed FROM public.company
  WHERE name IS NULL OR length(btrim(name)) = 0 OR length(name) > 300
     OR slug IS NULL OR length(btrim(slug)) = 0 OR length(slug) > 100
     OR length(icon) > 2048 OR name ~ E'[\\x01-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]' OR icon ~ E'[\\x01-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]'
     OR slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$';
  IF malformed > 0 THEN
    RAISE EXCEPTION 'Company reference seed refused: % malformed legacy rows; repair reviewed metadata before retry', malformed;
  END IF;
END $$;
--> statement-breakpoint
CREATE TABLE public.company_reference (
  id uuid PRIMARY KEY NOT NULL,
  name text NOT NULL,
  slug text NOT NULL,
  icon text,
  source text NOT NULL,
  verified_at timestamptz,
  created_at timestamptz DEFAULT now() NOT NULL,
  updated_at timestamptz DEFAULT now() NOT NULL,
  CONSTRAINT company_reference_name_check CHECK (length(btrim(name)) > 0 AND length(name) <= 300 AND name !~ E'[\\x01-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]'),
  CONSTRAINT company_reference_slug_check CHECK (length(btrim(slug)) > 0 AND length(slug) <= 100 AND slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'),
  CONSTRAINT company_reference_icon_check CHECK (icon IS NULL OR (length(icon) <= 2048 AND icon !~ E'[\\x01-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]')),
  CONSTRAINT company_reference_source_check CHECK (source IN ('legacy_seed', 'typesense')),
  CONSTRAINT company_reference_verification_check CHECK (source = 'legacy_seed' OR verified_at IS NOT NULL)
);
--> statement-breakpoint
INSERT INTO public.company_reference (id, name, slug, icon, source, created_at, updated_at)
SELECT id, name, slug, icon, 'legacy_seed', created_at AT TIME ZONE 'UTC', updated_at AT TIME ZONE 'UTC'
FROM public.company;
--> statement-breakpoint
-- Old releases can still insert legacy rows after the initial seed. Their display
-- updates may refresh legacy_seed only; they cannot overwrite verified identity.
CREATE FUNCTION public.company_reference_from_legacy() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  INSERT INTO public.company_reference (id, name, slug, icon, source, created_at, updated_at)
  VALUES (NEW.id, NEW.name, NEW.slug, NEW.icon, 'legacy_seed', NEW.created_at AT TIME ZONE 'UTC', NEW.updated_at AT TIME ZONE 'UTC')
  ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, slug = EXCLUDED.slug,
    icon = EXCLUDED.icon, updated_at = EXCLUDED.updated_at
  WHERE company_reference.source = 'legacy_seed';
  RETURN NEW;
END $$;
--> statement-breakpoint
CREATE TRIGGER company_reference_legacy_bridge
AFTER INSERT OR UPDATE OF name, slug, icon ON public.company
FOR EACH ROW EXECUTE FUNCTION public.company_reference_from_legacy();
--> statement-breakpoint
ALTER TABLE public.company_reference ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.company_reference FROM PUBLIC;
REVOKE ALL ON FUNCTION public.company_reference_from_legacy() FROM PUBLIC;
DO $$
DECLARE browser_role text;
BEGIN
  FOR browser_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated') LOOP
    EXECUTE format('REVOKE ALL ON TABLE public.company_reference FROM %I', browser_role);
    EXECUTE format('REVOKE ALL ON FUNCTION public.company_reference_from_legacy() FROM %I', browser_role);
  END LOOP;
END $$;
