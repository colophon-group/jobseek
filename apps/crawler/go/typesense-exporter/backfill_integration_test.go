package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

// Run against an isolated CI PostgreSQL service. This exercises the real
// keyset SQL, safe cutoff, session fence, projection, HTTP acknowledgements,
// failure/restart, and cursor save together, without production credentials.
func TestBackfillPostgresFenceAndRestart(t *testing.T) {
	dsn := os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("GO_TYPESENSE_TEST_DATABASE_URL is not set")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(context.Background())
	observer, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer observer.Close(context.Background())
	schema := pgx.Identifier{fmt.Sprintf("backfill_test_%d", time.Now().UnixNano())}.Sanitize()
	if _, err := conn.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		t.Fatal(err)
	}
	defer conn.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE")
	for _, db := range []*pgx.Conn{conn, observer} {
		if _, err := db.Exec(ctx, "SET search_path TO "+schema); err != nil {
			t.Fatal(err)
		}
	}
	_, err = conn.Exec(ctx, `
CREATE TABLE exporter_state (key text PRIMARY KEY, value text, updated_at timestamptz);
CREATE TABLE company (id uuid PRIMARY KEY, name text, slug text, icon text);
CREATE TABLE job_posting (
 id uuid PRIMARY KEY, company_id uuid, source_url text, is_active boolean,
 titles text[], locales text[], location_ids int[], location_types text[], employment_type text,
 salary_min bigint, salary_max bigint, salary_currency text, salary_period text, salary_eur bigint,
 experience_min numeric, experience_max numeric, occupation_id int, seniority_id int, technology_ids int[],
 description_r2_hash bigint, first_seen_at timestamptz, last_seen_at timestamptz, updated_at timestamptz
);
INSERT INTO company VALUES ('00000000-0000-0000-0000-000000000010','Fixture Co','fixture-co',NULL);
INSERT INTO job_posting (id,company_id,source_url,is_active,titles,updated_at)
VALUES ('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000010','https://example.test/1',true,ARRAY['First'],now()-interval '2 hours'),
       ('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000010','https://example.test/2',false,ARRAY['Second'],now()-interval '1 hour');`)
	if err != nil {
		t.Fatal(err)
	}
	existing := cursor{UpdatedAt: time.Now().UTC().Add(-3 * time.Hour).Truncate(time.Microsecond), ID: zeroUUID}
	if err := saveCursor(ctx, conn, existing); err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	documents := map[string]map[string]any{}
	requests := 0
	failSecond := true
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		requests++
		if r.URL.Path != "/collections/job_posting/documents/import" || r.URL.Query().Get("action") != "upsert" {
			t.Errorf("unexpected import endpoint: %s", r.URL)
		}
		var doc map[string]any
		if err := json.NewDecoder(r.Body).Decode(&doc); err != nil {
			t.Error(err)
		}
		documents[doc["id"].(string)] = doc // Model a write with a lost acknowledgement.
		if requests == 2 && failSecond {
			fmt.Fprint(w, `{}`)
		} else {
			fmt.Fprint(w, `{"success":true}`)
		}
	}))
	defer server.Close()
	newSource := func() *backfillDatabase {
		return &backfillDatabase{exporter{conn: conn, settings: exporterSettings{BatchLimit: 1}, mapsLoadedAt: time.Now()}}
	}
	write := func(ctx context.Context, docs []map[string]any) error {
		var acquired bool
		if err := observer.QueryRow(ctx, "SELECT pg_try_advisory_lock($1)", exportCursorFenceID).Scan(&acquired); err != nil || acquired {
			t.Fatalf("another exporter entered the scan fence: acquired=%v err=%v", acquired, err)
		}
		failed, err := importDocs(ctx, server.Client(), server.URL, "fixture-key", docs)
		if len(failed) != 0 {
			return fmt.Errorf("fixture import rejected")
		}
		return err
	}
	if _, err := backfill(ctx, newSource(), write); err == nil {
		t.Fatal("ambiguous acknowledgement accepted")
	}
	position, err := loadCursor(ctx, conn)
	if err != nil || position.ID != existing.ID || !position.UpdatedAt.Equal(existing.UpdatedAt) {
		t.Fatalf("cursor advanced after failed scan: %v %v", position, err)
	}
	mu.Lock()
	failSecond = false
	mu.Unlock()
	if total, err := backfill(ctx, newSource(), write); err != nil || total != 2 {
		t.Fatalf("replay total=%d error=%v", total, err)
	}
	position, err = loadCursor(ctx, conn)
	if err != nil || position.ID != "00000000-0000-0000-0000-000000000002" {
		t.Fatalf("wrong final cursor: %v %v", position, err)
	}
	var acquired bool
	if err := observer.QueryRow(ctx, "SELECT pg_try_advisory_lock($1)", exportCursorFenceID).Scan(&acquired); err != nil || !acquired {
		t.Fatalf("fence was not released: %v %v", acquired, err)
	}
	_, _ = observer.Exec(ctx, "SELECT pg_advisory_unlock($1)", exportCursorFenceID)
	mu.Lock()
	defer mu.Unlock()
	if requests != 4 || len(documents) != 2 {
		t.Fatalf("replay lost downstream documents: requests=%d documents=%d", requests, len(documents))
	}
	for id, doc := range documents {
		if doc["company_name"] != "Fixture Co" || doc["is_active"] != strings.HasSuffix(id, "1") {
			t.Fatalf("incorrect active/inactive projection: %v", doc)
		}
	}
}
