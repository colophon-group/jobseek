-- Running upgrade 0019 -> 0020

WITH incident_posting (board_slug, source_url) AS (
    VALUES
        (
            'epfl-hqc',
            'https://www.epfl.ch/labs/hqc/open-positions/?_jid=back-to-top-9641fb'
        ),
        (
            'epfl-hqc',
            'https://www.epfl.ch/labs/hqc/open-positions/?_jid=fabrication-and-characterization-granular-aluminum-987304'
        ),
        (
            'epfl-hqc',
            'https://www.epfl.ch/labs/hqc/open-positions/?_jid=fabrication-and-characterization-of-quantum-dots-i-0ecb86'
        ),
        (
            'epfl-hqc',
            'https://www.epfl.ch/labs/hqc/open-positions/?_jid=implementation-of-critical-qubit-readout-858a4e'
        ),
        (
            'epfl-hqc',
            'https://www.epfl.ch/labs/hqc/open-positions/?_jid=multimode-quantum-electrodynamics-with-atom-photon-e79156'
        ),
        (
            'epfl-hqc',
            'https://www.epfl.ch/labs/hqc/open-positions/?_jid=skip-to-content-96272c'
        ),
        (
            'epfl-lfim',
            'https://www.epfl.ch/labs/lfim/openings/?_jid=covalent-organic-frameworks-for-gold-recovery-from-810fc6'
        ),
        (
            'epfl-lfim',
            'https://www.epfl.ch/labs/lfim/openings/?_jid=development-of-sorbents-for-direct-air-capture-of--d63fcd'
        ),
        (
            'epfl-lfim',
            'https://www.epfl.ch/labs/lfim/openings/?_jid=optimizing-adsorbent-materials-for-gas-separations-c9fe69'
        ),
        (
            'epfl-lfim',
            'https://www.epfl.ch/labs/lfim/openings/?_jid=precious-metal-recovery-from-industrial-waste-stre-b68aae'
        )
)
UPDATE job_posting AS posting
SET is_active = false,
    next_scrape_at = NULL,
    updated_at = now()
FROM job_board AS board,
     incident_posting AS incident
WHERE posting.board_id = board.id
  AND board.board_slug = incident.board_slug
  AND posting.source_url = incident.source_url
  AND posting.is_active = true;

UPDATE alembic_version SET version_num='0020' WHERE alembic_version.version_num = '0019';
