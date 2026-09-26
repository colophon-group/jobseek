package main

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// The existing descriptions table is the queue. Claims and retries use its
// durable three-state protocol; this process does not introduce another queue.
type description struct {
	PostingID string
	Locale    string
	HTML      string
	Hash      int64
	Failures  int32
}

type descriptionStore struct{ db *pgxpool.Pool }

func (s descriptionStore) reap(ctx context.Context, staleAfter *time.Duration) (int64, error) {
	query := `WITH candidates AS MATERIALIZED (
		SELECT posting_id, locale FROM descriptions
		WHERE r2_uploaded IS NULL
		ORDER BY updated_at, posting_id, locale
		FOR UPDATE SKIP LOCKED LIMIT $1
	), reaped AS (
		UPDATE descriptions AS d SET r2_uploaded = false, updated_at = now()
		FROM candidates AS c
		WHERE d.posting_id = c.posting_id AND d.locale = c.locale
		RETURNING d.posting_id
	) SELECT count(*) FROM reaped`
	args := []any{500}
	if staleAfter != nil {
		query = `WITH candidates AS MATERIALIZED (
			SELECT posting_id, locale FROM descriptions
			WHERE r2_uploaded IS NULL AND updated_at < now() - make_interval(secs => $2::double precision)
			ORDER BY updated_at, posting_id, locale
			FOR UPDATE SKIP LOCKED LIMIT $1
		), reaped AS (
			UPDATE descriptions AS d SET r2_uploaded = false, updated_at = now()
			FROM candidates AS c
			WHERE d.posting_id = c.posting_id AND d.locale = c.locale
			RETURNING d.posting_id
		) SELECT count(*) FROM reaped`
		args = append(args, staleAfter.Seconds())
	}
	var count int64
	err := s.db.QueryRow(ctx, query, args...).Scan(&count)
	return count, err
}

func (s descriptionStore) claim(ctx context.Context, limit int) ([]description, error) {
	rows, err := s.db.Query(ctx, `WITH candidates AS MATERIALIZED (
		SELECT posting_id, locale FROM descriptions
		WHERE r2_uploaded = false AND r2_next_attempt_at <= now()
		ORDER BY r2_next_attempt_at, posting_id, locale
		FOR UPDATE SKIP LOCKED LIMIT $1
	) UPDATE descriptions AS d SET r2_uploaded = NULL, updated_at = now()
	FROM candidates AS c
	WHERE d.posting_id = c.posting_id AND d.locale = c.locale
	RETURNING d.posting_id::text, d.locale, d.html, d.hash, d.r2_upload_failures`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	claimed := make([]description, 0, limit)
	for rows.Next() {
		var d description
		if err := rows.Scan(&d.PostingID, &d.Locale, &d.HTML, &d.Hash, &d.Failures); err != nil {
			return nil, err
		}
		claimed = append(claimed, d)
	}
	return claimed, rows.Err()
}

func (s descriptionStore) complete(ctx context.Context, d description) (bool, error) {
	// A superseded description is never marked current. Keep both updates in
	// one transaction so a crash cannot leave a completed description without
	// the job_posting pointer used by readers and the exporter.
	tx, err := s.db.Begin(ctx)
	if err != nil {
		return false, err
	}
	defer tx.Rollback(context.Background())
	var marked bool
	err = tx.QueryRow(ctx, `UPDATE descriptions SET r2_uploaded = true,
		r2_upload_failures = 0, r2_next_attempt_at = '-infinity'::timestamptz
		WHERE posting_id = $1::uuid AND locale = $2 AND hash = $3
		RETURNING true`, d.PostingID, d.Locale, d.Hash).Scan(&marked)
	if err != nil {
		// pgx.ErrNoRows means a newer description replaced the claimed hash.
		if err == pgx.ErrNoRows {
			return false, nil
		}
		return false, err
	}
	_, err = tx.Exec(ctx, `UPDATE job_posting SET description_r2_hash = $2,
		to_be_enriched = true,
		updated_at = CASE WHEN description_r2_hash IS DISTINCT FROM $2
		THEN now() ELSE updated_at END
		WHERE id = $1::uuid`, d.PostingID, d.Hash)
	if err != nil {
		return false, err
	}
	if err := tx.Commit(ctx); err != nil {
		return false, err
	}
	return true, nil
}

func (s descriptionStore) retry(ctx context.Context, d description, failures int32, delay time.Duration) (bool, error) {
	var scheduled bool
	err := s.db.QueryRow(ctx, `UPDATE descriptions SET r2_uploaded = false,
		r2_upload_failures = $3, r2_next_attempt_at = now() + make_interval(secs => $4::double precision)
		WHERE posting_id = $1::uuid AND locale = $2 AND hash = $5
		RETURNING true`, d.PostingID, d.Locale, failures, delay.Seconds(), d.Hash).Scan(&scheduled)
	if err == pgx.ErrNoRows {
		return false, nil
	}
	return scheduled, err
}
