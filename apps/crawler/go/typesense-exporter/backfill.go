package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
)

// A backfill holds the existing exporter fence for the whole scan. Its durable
// cursor changes only after every document below the fixed safe cutoff has an
// acknowledgement. A failed run can therefore be replayed as idempotent upserts.
type backfillSource interface {
	Fence(context.Context, func() error) error
	Position(context.Context) (cursor, error)
	Cutoff(context.Context) (time.Time, error)
	Documents(context.Context, cursor, time.Time) ([]map[string]any, cursor, error)
	Save(context.Context, cursor) error
}

type backfillDatabase struct{ exporter }

func (b *backfillDatabase) Fence(ctx context.Context, work func() error) error {
	return withCursorFence(ctx, b.conn, work)
}
func (b *backfillDatabase) Position(ctx context.Context) (cursor, error) {
	return loadCursor(ctx, b.conn)
}
func (b *backfillDatabase) Cutoff(ctx context.Context) (time.Time, error) {
	return captureSafeCutoff(ctx, b.conn, nil)
}
func (b *backfillDatabase) Save(ctx context.Context, position cursor) error {
	return saveCursor(ctx, b.conn, position)
}
func (b *backfillDatabase) Documents(ctx context.Context, after cursor, cutoff time.Time) ([]map[string]any, cursor, error) {
	if b.mapsLoadedAt.IsZero() || time.Since(b.mapsLoadedAt) > 10*time.Minute {
		maps, err := loadMaps(ctx, b.conn)
		if err != nil {
			return nil, after, err
		}
		b.maps, b.mapsLoadedAt = maps, time.Now()
	}
	rows, next, err := fetchPostings(ctx, b.conn, after, cutoff, b.settings.BatchLimit)
	if err != nil {
		return nil, after, err
	}
	docs := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		doc, err := project(row, b.maps)
		if err != nil {
			return nil, after, err
		}
		docs = append(docs, doc)
	}
	return docs, next, nil
}

func backfill(ctx context.Context, source backfillSource, write func(context.Context, []map[string]any) error) (int, error) {
	total := 0
	err := source.Fence(ctx, func() error {
		slog.Info("backfill.cursor_fence_acquired")
		existing, err := source.Position(ctx)
		if err != nil {
			return err
		}
		cutoff, err := source.Cutoff(ctx)
		if err != nil {
			return err
		}
		position, _ := parseCursor("")
		for {
			if err := ctx.Err(); err != nil {
				return err
			}
			docs, next, err := source.Documents(ctx, position, cutoff)
			if err != nil {
				return err
			}
			if len(docs) == 0 {
				break
			}
			if !position.before(next) || !next.UpdatedAt.Before(cutoff) {
				return errors.New("backfill batch violates keyset order or cutoff")
			}
			if err := write(ctx, docs); err != nil {
				return err
			}
			position = next
			total += len(docs)
			if total%10000 < len(docs) {
				slog.Info("backfill.progress", "total", total)
			}
		}
		// Deleted rows or an older writer cutoff can put the scan tail behind
		// the live cursor. Preserve that cursor instead of rewinding the stream.
		if position.before(existing) {
			position = existing
		}
		if position.UpdatedAt.IsZero() {
			return nil // An empty initial index has no acknowledged cursor to save.
		}
		return source.Save(ctx, position)
	})
	return total, err
}

func importBackfillBatch(ctx context.Context, client *http.Client, baseURL, key string, docs []map[string]any, delay time.Duration) error {
	for attempt := 1; attempt <= 5; attempt++ {
		failed, err := importDocs(ctx, client, baseURL, key, docs)
		if err == nil && len(failed) != 0 {
			err = fmt.Errorf("Typesense rejected %d documents in backfill batch", len(failed))
		}
		if err == nil {
			return nil
		}
		if attempt == 5 || ctx.Err() != nil {
			return err
		}
		slog.Warn("backfill.typesense_upsert_retry", "attempt", attempt, "batch_size", len(docs), "error", err)
		timer := time.NewTimer(delay * time.Duration(1<<(attempt-1)))
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
	return errors.New("unreachable backfill retry state")
}

func runBackfill() (resultErr error) {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	started := time.Now()
	slog.Info("cron.start", "event", "cron.start", "job", "backfill-typesense")
	defer func() {
		status := "success"
		if resultErr != nil {
			status = "failure"
		}
		slog.Info("cron.complete", "event", "cron.complete", "job", "backfill-typesense", "status", status, "duration_s", time.Since(started).Seconds())
		pushBackfillMetrics(os.Getenv("CRAWLER_PUSHGATEWAY_URL"), resultErr == nil)
	}()
	settings, err := loadExporterSettings()
	if err != nil {
		return err
	}
	if raw := os.Getenv("EXPORT_BATCH_LIMIT"); raw != "" {
		limit, err := strconv.Atoi(raw)
		if err != nil || limit < 1 || limit > 2000 {
			return errors.New("EXPORT_BATCH_LIMIT must be 1..2000")
		}
		settings.BatchLimit = limit
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, 4*time.Hour)
	defer cancel()
	conn, err := pgx.Connect(ctx, settings.DBURL)
	if err != nil {
		return errors.New("could not connect to local PostgreSQL")
	}
	defer conn.Close(context.Background())
	client := &http.Client{Timeout: 120 * time.Second}
	defer client.CloseIdleConnections()
	source := &backfillDatabase{exporter{conn: conn, settings: settings}}
	total, err := backfill(ctx, source, func(ctx context.Context, docs []map[string]any) error {
		return importBackfillBatch(ctx, client, settings.TypesenseURL, settings.OperationsKey, docs, 2*time.Second)
	})
	if err != nil {
		return fmt.Errorf("backfill failed after %d acknowledged documents: %w", total, err)
	}
	slog.Info("backfill.completed", "total", total)
	return nil
}
