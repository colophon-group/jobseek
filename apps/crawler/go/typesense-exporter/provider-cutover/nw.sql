
DO $jobseek$
DECLARE
    nw_board_count integer;
BEGIN
    SELECT count(*)
    INTO nw_board_count
    FROM job_board
    WHERE board_slug = 'nw-careers';

    IF nw_board_count > 1 THEN
        RAISE EXCEPTION 'NW provider cutover found ambiguous nw-careers ownership';
    END IF;

    IF nw_board_count = 1 AND EXISTS (
        SELECT 1
        FROM job_posting AS canonical
        JOIN (VALUES
        ('https://jobs.nw-groupe.com/jobs/7465186-bess-project-manager-italy', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/bess-project-manager-italy_milano'),
        ('https://jobs.nw-groupe.com/jobs/8125397-ingenieur-automaticien-bess-h-f-cdi', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/ingenieur-automaticien-bess-h-f-cdi_paris'),
        ('https://jobs.nw-groupe.com/jobs/5985741-charge-du-suivi-des-financements-h-f-apprentissage', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/charge-du-suivi-des-financements-apprentissage_paris_NW_6089Kzx'),
        ('https://jobs.nw-groupe.com/jobs/8115338-charge-de-financement-h-f-cdi', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/charge-de-financement-h-f-cdi_paris'),
        ('https://jobs.nw-groupe.com/jobs/8113870-ingenieur-qualite-produit-maintenance-n3-h-f-stage', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/ingenieur-qualite-produit-maintenance-n3-h-f-stage_paris'),
        ('https://jobs.nw-groupe.com/jobs/8108098-analyste-foncier-h-f-apprentissage', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/analyste-foncier-h-f-apprentissage_lyon'),
        ('https://jobs.nw-groupe.com/jobs/7580949-purchasing-contract-manager-h-f-cdi', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/purchasing-contract-manager-h-f-cdi_paris_NW_qyklLVV'),
        ('https://jobs.nw-groupe.com/jobs/8011898-senior-erp-project-manager-h-f-cdi', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/senior-erp-migration-project-manager-h-f-cdi_paris'),
        ('https://jobs.nw-groupe.com/jobs/8010487-rnw-stage-chef-de-projet-marche-h-f', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/rnw-stage-chef-de-projet-marche-h-f_paris_NW_VdkN6eN')
        ) AS identity_map (legacy_url, canonical_url)
          ON canonical.source_url = identity_map.canonical_url
        WHERE canonical.board_id IS DISTINCT FROM (
            SELECT id FROM job_board WHERE board_slug = 'nw-careers'
        )
    ) THEN
        RAISE EXCEPTION 'NW provider cutover found foreign canonical URL ownership';
    END IF;
END
$jobseek$;

WITH identity_map (legacy_url, canonical_url) AS (
    VALUES
        ('https://jobs.nw-groupe.com/jobs/7465186-bess-project-manager-italy', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/bess-project-manager-italy_milano'),
        ('https://jobs.nw-groupe.com/jobs/8125397-ingenieur-automaticien-bess-h-f-cdi', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/ingenieur-automaticien-bess-h-f-cdi_paris'),
        ('https://jobs.nw-groupe.com/jobs/5985741-charge-du-suivi-des-financements-h-f-apprentissage', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/charge-du-suivi-des-financements-apprentissage_paris_NW_6089Kzx'),
        ('https://jobs.nw-groupe.com/jobs/8115338-charge-de-financement-h-f-cdi', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/charge-de-financement-h-f-cdi_paris'),
        ('https://jobs.nw-groupe.com/jobs/8113870-ingenieur-qualite-produit-maintenance-n3-h-f-stage', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/ingenieur-qualite-produit-maintenance-n3-h-f-stage_paris'),
        ('https://jobs.nw-groupe.com/jobs/8108098-analyste-foncier-h-f-apprentissage', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/analyste-foncier-h-f-apprentissage_lyon'),
        ('https://jobs.nw-groupe.com/jobs/7580949-purchasing-contract-manager-h-f-cdi', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/purchasing-contract-manager-h-f-cdi_paris_NW_qyklLVV'),
        ('https://jobs.nw-groupe.com/jobs/8011898-senior-erp-project-manager-h-f-cdi', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/senior-erp-migration-project-manager-h-f-cdi_paris'),
        ('https://jobs.nw-groupe.com/jobs/8010487-rnw-stage-chef-de-projet-marche-h-f', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/rnw-stage-chef-de-projet-marche-h-f_paris_NW_VdkN6eN')
)
UPDATE job_posting AS legacy
SET is_active = false,
    next_scrape_at = NULL,
    updated_at = now()
FROM job_board AS board,
     identity_map
WHERE legacy.board_id = board.id
  AND board.board_slug = 'nw-careers'
  AND legacy.source_url = identity_map.legacy_url
  AND legacy.is_active = true
  AND EXISTS (
      SELECT 1
      FROM job_posting AS canonical
      WHERE canonical.source_url = identity_map.canonical_url
        AND canonical.board_id = board.id
        AND canonical.id <> legacy.id
  );

WITH identity_map (legacy_url, canonical_url) AS (
    VALUES
        ('https://jobs.nw-groupe.com/jobs/7465186-bess-project-manager-italy', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/bess-project-manager-italy_milano'),
        ('https://jobs.nw-groupe.com/jobs/8125397-ingenieur-automaticien-bess-h-f-cdi', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/ingenieur-automaticien-bess-h-f-cdi_paris'),
        ('https://jobs.nw-groupe.com/jobs/5985741-charge-du-suivi-des-financements-h-f-apprentissage', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/charge-du-suivi-des-financements-apprentissage_paris_NW_6089Kzx'),
        ('https://jobs.nw-groupe.com/jobs/8115338-charge-de-financement-h-f-cdi', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/charge-de-financement-h-f-cdi_paris'),
        ('https://jobs.nw-groupe.com/jobs/8113870-ingenieur-qualite-produit-maintenance-n3-h-f-stage', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/ingenieur-qualite-produit-maintenance-n3-h-f-stage_paris'),
        ('https://jobs.nw-groupe.com/jobs/8108098-analyste-foncier-h-f-apprentissage', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/analyste-foncier-h-f-apprentissage_lyon'),
        ('https://jobs.nw-groupe.com/jobs/7580949-purchasing-contract-manager-h-f-cdi', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/purchasing-contract-manager-h-f-cdi_paris_NW_qyklLVV'),
        ('https://jobs.nw-groupe.com/jobs/8011898-senior-erp-project-manager-h-f-cdi', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/senior-erp-migration-project-manager-h-f-cdi_paris'),
        ('https://jobs.nw-groupe.com/jobs/8010487-rnw-stage-chef-de-projet-marche-h-f', 'https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/rnw-stage-chef-de-projet-marche-h-f_paris_NW_VdkN6eN')
)
UPDATE job_posting AS legacy
SET source_url = identity_map.canonical_url,
    updated_at = now()
FROM job_board AS board,
     identity_map
WHERE legacy.board_id = board.id
  AND board.board_slug = 'nw-careers'
  AND legacy.source_url = identity_map.legacy_url
  AND legacy.is_active = true
  AND NOT EXISTS (
      SELECT 1
      FROM job_posting AS canonical
      WHERE canonical.source_url = identity_map.canonical_url
        AND canonical.board_id = board.id
        AND canonical.id <> legacy.id
  );

UPDATE job_posting AS posting
SET is_active = false,
    next_scrape_at = NULL,
    updated_at = now()
FROM job_board AS board
WHERE posting.board_id = board.id
  AND board.board_slug = 'nw-careers'
  AND posting.source_url LIKE 'https://jobs.nw-groupe.com/jobs/%'
  AND posting.is_active = true
