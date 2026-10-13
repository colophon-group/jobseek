-- Running upgrade 0033 -> 0034

ALTER TABLE job_board ADD COLUMN tdm_reserved boolean NOT NULL DEFAULT false;

ALTER TABLE job_board ADD COLUMN tdm_reservation jsonb;

ALTER TABLE job_posting ADD COLUMN tdm_reserved boolean NOT NULL DEFAULT false;

ALTER TABLE job_posting ADD COLUMN tdm_reservation jsonb;

CREATE FUNCTION inherit_board_tdm_reservation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE reserved boolean;
        BEGIN
            -- Serialize inserts/reassignments with a board reservation so a
            -- concurrent new posting cannot escape the board-wide update.
            SELECT tdm_reserved INTO reserved FROM job_board
            WHERE id = NEW.board_id FOR SHARE;
            NEW.tdm_reserved := NEW.tdm_reserved OR COALESCE(reserved, false);
            RETURN NEW;
        END $$;
        CREATE TRIGGER posting_inherit_tdm_reservation
        BEFORE INSERT OR UPDATE OF board_id, tdm_reserved ON job_posting
        FOR EACH ROW EXECUTE FUNCTION inherit_board_tdm_reservation();

        CREATE FUNCTION propagate_board_tdm_reservation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.tdm_reserved AND NOT OLD.tdm_reserved THEN
                UPDATE job_posting SET tdm_reserved = true,
                    updated_at = clock_timestamp()
                WHERE board_id = NEW.id AND NOT tdm_reserved;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER board_propagate_tdm_reservation
        AFTER UPDATE OF tdm_reserved ON job_board
        FOR EACH ROW EXECUTE FUNCTION propagate_board_tdm_reservation();;

UPDATE alembic_version SET version_num='0034' WHERE alembic_version.version_num = '0033';
