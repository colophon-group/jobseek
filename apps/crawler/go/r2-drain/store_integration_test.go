package main

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Run against a disposable PostgreSQL instance with TEST_DATABASE_URL. The
// test makes its own schema and exercises the durable transitions, not SQL
// string shape or a mocked database.
func TestDescriptionStoreTransitions(t *testing.T) {
	url := os.Getenv("TEST_DATABASE_URL")
	if url == "" {
		t.Skip("TEST_DATABASE_URL is not set")
	}
	ctx := context.Background()
	admin, err := pgxpool.New(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	schema := fmt.Sprintf("go_drain_test_%d", time.Now().UnixNano())
	if _, err := admin.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		t.Fatal(err)
	}
	defer admin.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE")
	config, err := pgxpool.ParseConfig(url)
	if err != nil {
		t.Fatal(err)
	}
	config.ConnConfig.RuntimeParams["search_path"] = schema
	db, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, query := range []string{
		`CREATE TABLE job_posting (id uuid PRIMARY KEY, description_r2_hash bigint, to_be_enriched boolean, updated_at timestamptz DEFAULT now())`,
		`CREATE TABLE descriptions (posting_id uuid, locale text, html text, hash bigint, r2_uploaded boolean,
			r2_upload_failures integer DEFAULT 0, r2_next_attempt_at timestamptz DEFAULT '-infinity',
			updated_at timestamptz DEFAULT now(), PRIMARY KEY(posting_id, locale))`,
		`INSERT INTO job_posting(id, description_r2_hash, to_be_enriched) VALUES ('00000000-0000-0000-0000-000000000001', NULL, false)`,
		`INSERT INTO descriptions(posting_id, locale, html, hash, r2_uploaded) VALUES
			('00000000-0000-0000-0000-000000000001', 'en', '<p>old</p>', -123, false)`,
	} {
		if _, err := db.Exec(ctx, query); err != nil {
			t.Fatal(err)
		}
	}
	store := descriptionStore{db: db}
	claimed, err := store.claim(ctx, 1)
	if err != nil || len(claimed) != 1 || claimed[0].Hash != -123 {
		t.Fatalf("claim = %+v, %v", claimed, err)
	}
	// SKIP LOCKED claims no second copy of the already claimed description.
	claimedAgain, err := store.claim(ctx, 1)
	if err != nil || len(claimedAgain) != 0 {
		t.Fatalf("duplicate claim = %+v, %v", claimedAgain, err)
	}
	current, err := store.complete(ctx, claimed[0])
	if err != nil || !current {
		t.Fatalf("complete = %t, %v", current, err)
	}
	var uploaded bool
	var hash int64
	if err := db.QueryRow(ctx, `SELECT d.r2_uploaded, j.description_r2_hash FROM descriptions d
		JOIN job_posting j ON j.id = d.posting_id`).Scan(&uploaded, &hash); err != nil || !uploaded || hash != -123 {
		t.Fatalf("complete state uploaded=%t hash=%d error=%v", uploaded, hash, err)
	}
	// A newer row cannot be made current by the old in-flight PUT.
	_, err = db.Exec(ctx, `UPDATE descriptions SET html='<p>new</p>', hash=456, r2_uploaded=false WHERE locale='en'`)
	if err != nil {
		t.Fatal(err)
	}
	current, err = store.complete(ctx, claimed[0])
	if err != nil || current {
		t.Fatalf("superseded complete = %t, %v", current, err)
	}
	newClaim, err := store.claim(ctx, 1)
	if err != nil || len(newClaim) != 1 || newClaim[0].Hash != 456 {
		t.Fatalf("new claim = %+v, %v", newClaim, err)
	}
	scheduled, err := store.retry(ctx, newClaim[0], 1, 5*time.Second)
	if err != nil || !scheduled {
		t.Fatalf("retry = %t, %v", scheduled, err)
	}
	var failures int32
	var next time.Time
	if err := db.QueryRow(ctx, `SELECT r2_uploaded, r2_upload_failures, r2_next_attempt_at FROM descriptions`).Scan(&uploaded, &failures, &next); err != nil || uploaded || failures != 1 || !next.After(time.Now()) {
		t.Fatalf("retry state uploaded=%t failures=%d next=%s error=%v", uploaded, failures, next, err)
	}
	// Run the actual consumer through a signed TLS PUT and verify that the
	// object body and committed database pointer describe the same version.
	client, server := testR2(t, func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if string(body) != "<p>new</p>" || r.URL.Path != "/fixture-bucket/job/00000000-0000-0000-0000-000000000001/en/latest.html" {
			t.Errorf("unexpected object: path=%s body=%q", r.URL.Path, body)
		}
		w.WriteHeader(http.StatusOK)
	})
	defer server.Close()
	_, err = db.Exec(ctx, `UPDATE descriptions SET r2_uploaded=NULL WHERE locale='en'`)
	if err != nil {
		t.Fatal(err)
	}
	consumerCtx, cancelConsumer := context.WithCancel(ctx)
	work := make(chan description, 1)
	done := make(chan struct{})
	go func() {
		defer close(done)
		consume(consumerCtx, store, client, work,
			settings{RetryBase: 5 * time.Second, RetryMax: 900 * time.Second},
			newMetrics(), slog.New(slog.NewTextHandler(io.Discard, nil)))
	}()
	work <- newClaim[0]
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if err := db.QueryRow(ctx, `SELECT d.r2_uploaded IS TRUE, j.description_r2_hash FROM descriptions d
			JOIN job_posting j ON j.id = d.posting_id`).Scan(&uploaded, &hash); err != nil {
			t.Fatal(err)
		}
		if uploaded && hash == 456 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	cancelConsumer()
	<-done
	if !uploaded || hash != 456 {
		t.Fatalf("consumer did not converge: uploaded=%t hash=%d", uploaded, hash)
	}
	// Startup reaps a claim regardless of age; periodic reaping waits for age.
	_, err = db.Exec(ctx, `UPDATE descriptions SET r2_uploaded=NULL, updated_at=now() WHERE locale='en'`)
	if err != nil {
		t.Fatal(err)
	}
	stale := 10 * time.Minute
	if count, err := store.reap(ctx, &stale); err != nil || count != 0 {
		t.Fatalf("fresh periodic reap = %d, %v", count, err)
	}
	if count, err := store.reap(ctx, nil); err != nil || count != 1 {
		t.Fatalf("startup reap = %d, %v", count, err)
	}
	// Description and posting pointer must commit together.
	_, err = db.Exec(ctx, `UPDATE descriptions SET r2_uploaded=NULL WHERE locale='en'`)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(ctx, `ALTER TABLE job_posting DROP COLUMN to_be_enriched`)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.complete(ctx, newClaim[0]); err == nil {
		t.Fatal("expected pointer update failure")
	}
	var isNull bool
	if err := db.QueryRow(ctx, `SELECT r2_uploaded IS NULL FROM descriptions`).Scan(&isNull); err != nil || !isNull {
		t.Fatalf("description commit escaped failed transaction: null=%t error=%v", isNull, err)
	}
}
