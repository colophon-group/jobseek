package main

import (
	"context"
	_ "embed"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/jackc/pgx/v5"
)

const (
	exportCursorFenceID int64 = 0x4A4F425345454B
	cdcWriterBarrierID  int64 = 0x4344434C4F434B
	typesenseCursorKey        = "last_export_ts:typesense:job_posting"
)

//go:embed cdc_cutoff.sql
var cdcCutoffSQL string

// withCursorFence serializes one complete read/import/cursor-save critical
// section with Python export, backfill, reconciliation, and operator repair.
// The session lock is released on success or error; an uncertain release
// closes the database connection so PostgreSQL drops the session lock.
func withCursorFence(ctx context.Context, conn *pgx.Conn, work func() error) (resultErr error) {
	for {
		var acquired bool
		if err := conn.QueryRow(ctx, "SELECT pg_try_advisory_lock($1::bigint)", exportCursorFenceID).Scan(&acquired); err != nil {
			// A canceled query can have acquired the session lock before the
			// client sees the error. Drop the session to remove that ambiguity.
			closeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = conn.Close(closeCtx)
			return fmt.Errorf("acquire export cursor fence: %w", err)
		}
		if acquired {
			break
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Second):
		}
	}
	defer func() {
		unlockCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		var released bool
		unlockErr := conn.QueryRow(unlockCtx, "SELECT pg_advisory_unlock($1::bigint)", exportCursorFenceID).Scan(&released)
		if unlockErr != nil || !released {
			closeCtx, closeCancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer closeCancel()
			_ = conn.Close(closeCtx)
			if unlockErr == nil {
				unlockErr = errors.New("PostgreSQL did not hold the export cursor fence")
			}
			resultErr = errors.Join(resultErr, fmt.Errorf("release export cursor fence: %w", unlockErr))
		}
	}()
	return work()
}

// captureSafeCutoff is the same one-statement writer-barrier query used by
// Python. Unknown still-held writer locks fail closed; rows stamped at or
// after the strict cutoff remain for a later tick.
func captureSafeCutoff(ctx context.Context, conn *pgx.Conn, metrics *exporterMetrics) (time.Time, error) {
	var captured, cutoff time.Time
	var active, released, unknown int32
	err := conn.QueryRow(ctx, cdcCutoffSQL, cdcWriterBarrierID).Scan(&captured, &cutoff, &active, &released, &unknown)
	if err != nil {
		return time.Time{}, fmt.Errorf("capture CDC cutoff: %w", err)
	}
	if metrics != nil {
		metrics.recordCDC(captured.Sub(cutoff), active, released, unknown)
	}
	if unknown > 0 {
		return time.Time{}, fmt.Errorf("CDC cutoff has %d unidentifiable active writers", unknown)
	}
	if cutoff.After(captured) || cutoff.IsZero() {
		return time.Time{}, errors.New("PostgreSQL returned an invalid CDC cutoff")
	}
	if released > 0 {
		slog.Info("cdc_snapshot_cutoff.writer_released_during_scan", "released_writers", released)
	}
	if delay := captured.Sub(cutoff); delay >= 30*time.Second {
		slog.Warn("cdc_snapshot_cutoff.delayed", "delay_s", delay.Seconds(), "active_writers", active)
	}
	return cutoff, nil
}

func loadCursor(ctx context.Context, conn *pgx.Conn) (cursor, error) {
	var value string
	err := conn.QueryRow(ctx, "SELECT value FROM exporter_state WHERE key = $1", typesenseCursorKey).Scan(&value)
	if errors.Is(err, pgx.ErrNoRows) {
		return parseCursor("")
	}
	if err != nil {
		return cursor{}, fmt.Errorf("load Typesense cursor: %w", err)
	}
	return parseCursor(value)
}

func saveCursor(ctx context.Context, conn *pgx.Conn, position cursor) error {
	encoded, err := position.encode()
	if err != nil {
		return err
	}
	_, err = conn.Exec(ctx,
		"INSERT INTO exporter_state (key, value, updated_at) VALUES ($1, $2, now()) "+
			"ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()",
		typesenseCursorKey, encoded)
	if err != nil {
		return fmt.Errorf("save Typesense cursor: %w", err)
	}
	return nil
}
