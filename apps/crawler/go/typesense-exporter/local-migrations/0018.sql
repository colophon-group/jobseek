-- Running upgrade 0017 -> 0018

ALTER TABLE cross_store_reconciliation_state
        ADD COLUMN cycle_detected BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN last_detected BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN cycle_payload_mismatch BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN last_payload_mismatch BIGINT NOT NULL DEFAULT 0;

ALTER TABLE cross_store_reconciliation_run
        ADD COLUMN payload_mismatch BIGINT NOT NULL DEFAULT 0;

UPDATE cross_store_reconciliation_state SET
            next_partition = 0,
            cycle_id = NULL,
            cycle_started_at = NULL,
            cycle_runtime_seconds = 0,
            cycle_local_rows = 0,
            cycle_local_active = 0,
            cycle_remote_rows = 0,
            cycle_remote_active = 0,
            cycle_detected = 0,
            cycle_missing_remote = 0,
            cycle_state_mismatch = 0,
            cycle_payload_mismatch = 0,
            cycle_remote_only_active = 0,
            cycle_remote_only_inactive = 0,
            cycle_repaired = 0,
            last_detected = last_missing_remote + last_state_mismatch
                + last_remote_only_active + last_remote_only_inactive,
            updated_at = clock_timestamp()
        WHERE target = 'typesense';

UPDATE alembic_version SET version_num='0018' WHERE alembic_version.version_num = '0017';
