package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

const reconciliationLockID int64 = 0x5245434F4E434C

type reconciliationSummary struct {
	RunID           string `json:"run_id"`
	Mode            string `json:"mode"`
	Target          string `json:"target_scope"`
	Partitions      int    `json:"partitions_completed"`
	Local           int    `json:"checked_local"`
	Remote          int    `json:"checked_remote"`
	Detected        int    `json:"detected"`
	PayloadMismatch int    `json:"payload_mismatch"`
	Repaired        int    `json:"repaired"`
	Unresolved      int    `json:"unresolved"`
}

func (s *reconciliationSummary) add(result reconciliationResult, completed bool) {
	if completed {
		s.Partitions++
	}
	s.Local += result.LocalRows
	s.Remote += result.RemoteRows
	s.Detected += result.Detected
	s.PayloadMismatch += result.PayloadMismatch
	s.Repaired += result.Repaired
	s.Unresolved += result.Unresolved
}

func reconciliationRunID() (string, error) {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", err
	}
	raw[6] = raw[6]&0x0f | 0x40
	raw[8] = raw[8]&0x3f | 0x80
	return reconciliationUUID(hex.EncodeToString(raw[:]))
}

func rollbackReconciliationTransaction(tx pgx.Tx) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = tx.Rollback(ctx)
}

var reconciliationStatColumns = []string{
	"runtime_seconds", "local_rows", "local_active", "remote_rows", "remote_active",
	"detected", "missing_remote", "state_mismatch", "payload_mismatch",
	"remote_only_active", "remote_only_inactive", "repaired",
}

func reconciliationStatValues(result reconciliationResult) []any {
	return []any{result.Duration, result.LocalRows, result.LocalActive, result.RemoteRows, result.RemoteActive,
		result.Detected, result.Missing, result.StateMismatch, result.PayloadMismatch, result.RemoteOnlyActive, result.RemoteOnlyInactive, result.Repaired}
}

func (d *reconciliationDatabase) startRun(ctx context.Context, summary reconciliationSummary) error {
	// The caller holds the global reconciliation lock, so prior running ledger
	// entries are interrupted processes, not concurrent owners.
	if _, err := d.conn.Exec(ctx, `UPDATE cross_store_reconciliation_run SET status='interrupted', completed_at=clock_timestamp(), error_class='InterruptedRun' WHERE status='running'`); err != nil {
		return err
	}
	if _, err := d.conn.Exec(ctx, `DELETE FROM cross_store_reconciliation_run WHERE completed_at < clock_timestamp()-interval '180 days'`); err != nil {
		return err
	}
	_, err := d.conn.Exec(ctx, `INSERT INTO cross_store_reconciliation_run (run_id,mode,target_scope) VALUES ($1::uuid,$2,'typesense')`, summary.RunID, summary.Mode)
	return err
}

func (d *reconciliationDatabase) persistRun(ctx context.Context, summary reconciliationSummary) error {
	tag, err := d.conn.Exec(ctx, `UPDATE cross_store_reconciliation_run SET partitions_completed=$2, checked_local=$3, checked_remote=$4, detected=$5, payload_mismatch=$6, repaired=$7, unresolved=$8 WHERE run_id=$1::uuid`,
		summary.RunID, summary.Partitions, summary.Local, summary.Remote, summary.Detected, summary.PayloadMismatch, summary.Repaired, summary.Unresolved)
	if err == nil && tag.RowsAffected() != 1 {
		return errors.New("reconciliation run ledger disappeared")
	}
	return err
}

func (d *reconciliationDatabase) finishRun(ctx context.Context, summary reconciliationSummary, status string, errorClass *string) error {
	if err := d.persistRun(ctx, summary); err != nil {
		return err
	}
	tag, err := d.conn.Exec(ctx, `UPDATE cross_store_reconciliation_run SET completed_at=clock_timestamp(), status=$2, error_class=$3 WHERE run_id=$1::uuid`, summary.RunID, status, errorClass)
	if err == nil && tag.RowsAffected() != 1 {
		return errors.New("reconciliation run ledger disappeared")
	}
	return err
}

func (d *reconciliationDatabase) ensureCycle(ctx context.Context, fresh bool) (int, error) {
	tx, err := d.conn.Begin(ctx)
	if err != nil {
		return 0, err
	}
	defer rollbackReconciliationTransaction(tx)
	var partition int
	var cycleID *string
	if err := tx.QueryRow(ctx, `SELECT next_partition,cycle_id::text FROM cross_store_reconciliation_state WHERE target='typesense' FOR UPDATE`).Scan(&partition, &cycleID); err != nil {
		return 0, err
	}
	assignments := []string{"last_attempt_at=clock_timestamp()", "last_outcome='progress'", "last_error_class=NULL", "last_unresolved=0", "updated_at=clock_timestamp()"}
	args := []any{}
	if fresh || cycleID == nil {
		id, err := reconciliationRunID()
		if err != nil {
			return 0, err
		}
		args = append(args, id)
		assignments = append(assignments, "next_partition=0", "cycle_id=$1::uuid", "cycle_started_at=clock_timestamp()")
		for _, column := range reconciliationStatColumns {
			assignments = append(assignments, "cycle_"+column+"=0")
		}
		partition = 0
	}
	if _, err := tx.Exec(ctx, "UPDATE cross_store_reconciliation_state SET "+strings.Join(assignments, ",")+" WHERE target='typesense'", args...); err != nil {
		return 0, err
	}
	if _, _, err := reconciliationBounds(partition); err != nil {
		return 0, err
	}
	return partition, tx.Commit(ctx)
}

func (d *reconciliationDatabase) advance(ctx context.Context, result reconciliationResult, bootstrap *bool) (bool, error) {
	if result.Unresolved != 0 {
		return false, errors.New("cannot advance an unresolved reconciliation partition")
	}
	tx, err := d.conn.Begin(ctx)
	if err != nil {
		return false, err
	}
	defer rollbackReconciliationTransaction(tx)
	assignments := make([]string, 0, len(reconciliationStatColumns))
	args := []any{result.Partition}
	for i, column := range reconciliationStatColumns {
		assignments = append(assignments, fmt.Sprintf("cycle_%s=cycle_%s+$%d", column, column, i+2))
	}
	args = append(args, reconciliationStatValues(result)...)
	tag, err := tx.Exec(ctx, "UPDATE cross_store_reconciliation_state SET "+strings.Join(assignments, ",")+" WHERE target='typesense' AND next_partition=$1 AND cycle_id IS NOT NULL", args...)
	if err != nil {
		return false, err
	}
	if tag.RowsAffected() != 1 {
		return false, errors.New("reconciliation partition cursor changed or disappeared")
	}
	completed := result.Partition == reconciliationPartitions-1
	assignments = []string{"bootstrap_complete=COALESCE($1::boolean,bootstrap_complete)", "last_attempt_at=clock_timestamp()", "last_error_class=NULL", "last_unresolved=0", "updated_at=clock_timestamp()"}
	if completed {
		assignments = append(assignments, "next_partition=0", "cycle_id=NULL", "last_started_at=cycle_started_at", "cycle_started_at=NULL", "last_success_at=clock_timestamp()", "last_outcome=CASE WHEN cycle_repaired>0 THEN 'repaired' ELSE 'clean' END")
		for _, column := range reconciliationStatColumns {
			last := column
			if column == "runtime_seconds" {
				last = "duration_seconds"
			}
			assignments = append(assignments, "last_"+last+"=cycle_"+column, "cycle_"+column+"=0")
		}
	} else {
		assignments = append(assignments, "next_partition=next_partition+1", "last_outcome='progress'")
	}
	if _, err := tx.Exec(ctx, "UPDATE cross_store_reconciliation_state SET "+strings.Join(assignments, ",")+" WHERE target='typesense'", bootstrap); err != nil {
		return false, err
	}
	return completed, tx.Commit(ctx)
}

func (d *reconciliationDatabase) bootstrapComplete(ctx context.Context) (bool, error) {
	var complete bool
	err := d.conn.QueryRow(ctx, `SELECT bootstrap_complete FROM cross_store_reconciliation_state WHERE target='typesense'`).Scan(&complete)
	return complete, err
}

func (d *reconciliationDatabase) recordFailure(ctx context.Context, errorClass string, unresolved int) error {
	tag, err := d.conn.Exec(ctx, `UPDATE cross_store_reconciliation_state SET last_attempt_at=clock_timestamp(), last_outcome='failed', last_unresolved=$1, last_error_class=$2, updated_at=clock_timestamp() WHERE target='typesense'`, unresolved, errorClass)
	if err == nil && tag.RowsAffected() != 1 {
		return errors.New("reconciliation state disappeared")
	}
	return err
}
