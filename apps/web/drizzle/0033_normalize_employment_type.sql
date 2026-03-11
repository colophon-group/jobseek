-- Phase 2: Normalize employment_type values + add CHECK constraint
UPDATE job_posting SET employment_type = CASE
  WHEN lower(trim(employment_type)) IN ('full-time','full time','fulltime','permanent','permanent employment','permanent full-time','regular','employee / full-time','eor / full-time','graduate','other','other_employment_type','festanstellung','unbefristet','vollzeit','regulär','cdi','emploi fixe','temps plein','plein temps','libéral','impiego fisso','tempo indeterminato','tempo pieno','a tempo pieno') THEN 'full_time'
  WHEN lower(trim(employment_type)) IN ('part-time','part time','parttime','teilzeit','temps partiel','mi-temps','tempo parziale') THEN 'part_time'
  WHEN lower(trim(employment_type)) IN ('contract','contractor','temporary','temporary positions','fixed term','fixed term (fixed term)','fixed term / full-time','befristet','zeitarbeit','freiberuflich','freelancer','cdd','intérim','intérimaire','freelance','indépendant','tempo determinato','a tempo determinato','contratto a termine','lavoro interinale','collaborazione') THEN 'contract'
  WHEN lower(trim(employment_type)) IN ('internship','intern','werkstudent','praktikum','praktikant','lernende','ausbildung','azubi','stage','alternance','apprentissage','stagiaire','tirocinio','apprendistato') THEN 'internship'
  WHEN lower(trim(employment_type)) IN ('full time or part time','full-time, part-time','permanent full-time or part-time','temporary positions, full-time','full_time, part_time','vollzeit oder teilzeit','voll- oder teilzeit','voll-/teilzeit','temps plein ou partiel','tempo pieno o parziale') THEN 'full_or_part'
  WHEN employment_type = 'full_time' THEN 'full_time'
  WHEN employment_type = 'part_time' THEN 'part_time'
  WHEN employment_type = 'contract' THEN 'contract'
  WHEN employment_type = 'internship' THEN 'internship'
  WHEN employment_type = 'full_or_part' THEN 'full_or_part'
  ELSE 'full_time'
END
WHERE employment_type IS NOT NULL;

ALTER TABLE job_posting ADD CONSTRAINT chk_employment_type
  CHECK (employment_type IS NULL OR employment_type IN ('full_time','part_time','contract','internship','full_or_part'))
  NOT VALID;

ALTER TABLE job_posting VALIDATE CONSTRAINT chk_employment_type;
