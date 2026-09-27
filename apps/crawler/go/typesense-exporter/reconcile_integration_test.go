package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"regexp"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func reconciliationIntegrationDatabase(t *testing.T) (context.Context, *reconciliationDatabase, *pgx.Conn) {
	t.Helper()
	dsn := os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("GO_TYPESENSE_TEST_DATABASE_URL is not set")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	t.Cleanup(cancel)
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close(context.Background()) })
	observer, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = observer.Close(context.Background()) })
	schema := pgx.Identifier{fmt.Sprintf("reconciliation_test_%d", time.Now().UnixNano())}.Sanitize()
	if _, err := conn.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = conn.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE") })
	for _, db := range []*pgx.Conn{conn, observer} {
		if _, err := db.Exec(ctx, "SET search_path TO "+schema); err != nil {
			t.Fatal(err)
		}
	}
	// Execute the actual authoritative state migrations rather than a second
	// hand-maintained ledger schema that could accidentally agree with Go bugs.
	statements := regexp.MustCompile(`(?s)op\.execute\("""(.*?)"""\)`)
	for _, migration := range []string{"0013_add_cross_store_reconciliation_state.py", "0018_track_reconciliation_payload_drift.py"} {
		raw, err := os.ReadFile("../../src/migrations/versions/" + migration)
		if err != nil {
			t.Fatal(err)
		}
		upgrade := strings.Split(string(raw), "def downgrade")[0]
		matches := statements.FindAllStringSubmatch(upgrade, -1)
		if len(matches) == 0 {
			t.Fatal("migration fixture found no SQL")
		}
		for _, match := range matches {
			if _, err := conn.Exec(ctx, match[1]); err != nil {
				t.Fatal(err)
			}
		}
	}
	_, err = conn.Exec(ctx, `
CREATE TABLE company (id uuid PRIMARY KEY,name text,slug text,icon text);
CREATE TABLE job_posting (
 id uuid PRIMARY KEY,company_id uuid,source_url text,is_active boolean,titles text[],locales text[],
 location_ids int[],location_types text[],employment_type text,salary_min bigint,salary_max bigint,
 salary_currency text,salary_period text,salary_eur bigint,experience_min numeric,experience_max numeric,
 occupation_id int,seniority_id int,technology_ids int[],tdm_reserved boolean NOT NULL DEFAULT false, description_r2_hash bigint,first_seen_at timestamptz,last_seen_at timestamptz);
INSERT INTO company VALUES ('00000000-0000-0000-0000-000000000010','Fixture Co','fixture-co',NULL);
INSERT INTO job_posting (id,company_id,is_active,titles) VALUES
 ('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000010',true,ARRAY['First']),
 ('ff000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000010',false,ARRAY['Last']);`)
	if err != nil {
		t.Fatal(err)
	}
	return ctx, &reconciliationDatabase{conn: conn}, observer
}

func TestReconciliationPostgresRepairFailureRestartAndFullProof(t *testing.T) {
	ctx, database, observer := reconciliationIntegrationDatabase(t)
	var mutex sync.Mutex
	documents := map[string]map[string]any{
		"00000000-0000-0000-0000-000000000099": fixtureReconciliationDocument("00000000-0000-0000-0000-000000000099", "orphan"),
		"legacy/orphan":                        {"id": "legacy/orphan", "is_active": false},
	}
	poison := true
	imports, deletes := 0, 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mutex.Lock()
		defer mutex.Unlock()
		switch {
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/import"):
			var acquired bool
			if err := observer.QueryRow(ctx, `SELECT pg_try_advisory_lock($1::bigint)`, exportCursorFenceID).Scan(&acquired); err != nil || acquired {
				t.Errorf("import escaped cursor fence: acquired=%v error=%v", acquired, err)
			}
			decoder := json.NewDecoder(r.Body)
			decoder.UseNumber()
			for {
				var document map[string]any
				err := decoder.Decode(&document)
				if err == io.EOF {
					break
				}
				if err != nil {
					t.Error(err)
					return
				}
				documents[document["id"].(string)] = document
				imports++
				if poison {
					fmt.Fprintln(w, `{}`)
				} else {
					fmt.Fprintln(w, `{"success":true}`)
				}
			}
		case r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/export"):
			bucket := strings.TrimPrefix(r.URL.Query().Get("filter_by"), "reconciliation_bucket:=")
			ids := make([]string, 0, len(documents))
			for id := range documents {
				ids = append(ids, id)
			}
			sort.Strings(ids)
			for _, id := range ids {
				document := documents[id]
				if bucket == "" || document["reconciliation_bucket"] == bucket {
					_ = json.NewEncoder(w).Encode(document)
				}
			}
		case r.Method == http.MethodDelete:
			id := strings.TrimPrefix(r.URL.Path, "/collections/job_posting/documents/")
			delete(documents, id)
			deletes++
			fmt.Fprintln(w, `{}`)
		default:
			t.Errorf("unexpected reconciliation request %s %s", r.Method, r.URL.Path)
			w.WriteHeader(500)
		}
	}))
	defer server.Close()
	index := reconciliationHTTP{Client: server.Client(), BaseURL: server.URL, Key: "fixture-key"}
	refresh := func(context.Context) error { return nil } // Fixture postings have no taxonomy references.
	options := reconciliationOptions{Repair: true, MaxPartitions: 1}
	summary, err := runReconciliation(ctx, database, index, options, refresh)
	if err == nil || summary.Partitions != 0 || summary.Unresolved != 2 {
		t.Fatalf("ambiguous write advanced proof: %+v %v", summary, err)
	}
	mutex.Lock()
	initialImports := imports
	mutex.Unlock()
	if initialImports != 1 {
		t.Fatal("fixture failed before its ambiguous write reached Typesense")
	}
	var partition, unresolved int
	var outcome, status string
	if err := database.conn.QueryRow(ctx, `SELECT next_partition,last_unresolved,last_outcome FROM cross_store_reconciliation_state WHERE target='typesense'`).Scan(&partition, &unresolved, &outcome); err != nil {
		t.Fatal(err)
	}
	if partition != 0 || unresolved != 2 || outcome != "failed" {
		t.Fatalf("failed partition state %d %d %s", partition, unresolved, outcome)
	}
	if err := database.conn.QueryRow(ctx, `SELECT status FROM cross_store_reconciliation_run WHERE run_id=$1::uuid`, summary.RunID).Scan(&status); err != nil || status != "failed" {
		t.Fatalf("failed run not durable: %s %v", status, err)
	}
	mutex.Lock()
	poison = false
	mutex.Unlock()
	summary, err = runReconciliation(ctx, database, index, options, refresh)
	if err != nil || summary.Partitions != 1 || summary.Unresolved != 0 {
		t.Fatalf("resume failed: %+v %v", summary, err)
	}
	if err := database.conn.QueryRow(ctx, `SELECT next_partition FROM cross_store_reconciliation_state WHERE target='typesense'`).Scan(&partition); err != nil || partition != 1 {
		t.Fatalf("resume did not advance once: %d %v", partition, err)
	}
	options.Full, options.Fresh = true, true
	summary, err = runReconciliation(ctx, database, index, options, refresh)
	if err != nil || summary.Partitions != 256 || summary.Local != 2 || summary.Unresolved != 0 {
		t.Fatalf("full proof failed: %+v %v", summary, err)
	}
	var bootstrap bool
	var lastRows int
	if err := database.conn.QueryRow(ctx, `SELECT next_partition,bootstrap_complete,last_local_rows,last_outcome FROM cross_store_reconciliation_state WHERE target='typesense'`).Scan(&partition, &bootstrap, &lastRows, &outcome); err != nil {
		t.Fatal(err)
	}
	if partition != 0 || !bootstrap || lastRows != 2 || outcome != "repaired" {
		t.Fatalf("cycle proof not durable: %d %v %d %s", partition, bootstrap, lastRows, outcome)
	}
	mutex.Lock()
	count, removed, written := len(documents), deletes, imports
	mutex.Unlock()
	if count != 2 || removed != 2 || written != 2 {
		t.Fatalf("unexpected index effects: documents=%d deletes=%d imports=%d", count, removed, written)
	}
	receipt, err := reconciliationReadinessReceipt(ctx, database, summary, strings.Repeat("a", 64))
	if err != nil {
		t.Fatal(err)
	}
	raw, err := base64.RawURLEncoding.DecodeString(receipt)
	if err != nil {
		t.Fatal(err)
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatal(err)
	}
	if payload["authoritativeCount"] != float64(2) || payload["partitions"] != float64(256) {
		t.Fatalf("wrong readiness proof: %s", raw)
	}
	if _, err := database.conn.Exec(ctx, `UPDATE cross_store_reconciliation_run SET unresolved=1 WHERE run_id=$1::uuid`, summary.RunID); err != nil {
		t.Fatal(err)
	}
	if _, err := reconciliationReadinessReceipt(ctx, database, summary, strings.Repeat("a", 64)); err == nil {
		t.Fatal("readiness trusted stale in-memory summary")
	}
}

func TestReconciliationPostgresLockCancellationAndCursorGuard(t *testing.T) {
	ctx, database, observer := reconciliationIntegrationDatabase(t)
	if _, err := observer.Exec(ctx, `SELECT pg_advisory_lock($1::bigint)`, reconciliationLockID); err != nil {
		t.Fatal(err)
	}
	refresh := func(context.Context) error { return nil }
	options := reconciliationOptions{Repair: true, Full: true, Fresh: true, MaxPartitions: 16}
	if _, err := runReconciliation(ctx, database, repairFixture(), options, refresh); err == nil {
		t.Fatal("fresh proof accepted a contended lock")
	}
	if _, err := observer.Exec(ctx, `SELECT pg_advisory_unlock($1::bigint)`, reconciliationLockID); err != nil {
		t.Fatal(err)
	}
	cancelCtx, cancel := context.WithCancel(ctx)
	summary, err := runReconciliation(cancelCtx, database, repairFixture(), options, func(context.Context) error { cancel(); return context.Canceled })
	if err == nil {
		t.Fatal("cancelled proof succeeded")
	}
	var status string
	var partition int
	if err := database.conn.QueryRow(ctx, `SELECT status FROM cross_store_reconciliation_run WHERE run_id=$1::uuid`, summary.RunID).Scan(&status); err != nil || status != "interrupted" {
		t.Fatalf("cancellation ledger: %s %v", status, err)
	}
	if err := database.conn.QueryRow(ctx, `SELECT next_partition FROM cross_store_reconciliation_state WHERE target='typesense'`).Scan(&partition); err != nil || partition != 0 {
		t.Fatalf("cancellation advanced cursor: %d %v", partition, err)
	}
	if _, err := database.advance(ctx, reconciliationResult{Partition: 1, LocalRows: 42}, nil); err == nil {
		t.Fatal("advanced a mismatching cursor")
	}
	var rows int
	if err := database.conn.QueryRow(ctx, `SELECT cycle_local_rows FROM cross_store_reconciliation_state WHERE target='typesense'`).Scan(&rows); err != nil || rows != 0 {
		t.Fatalf("rejected advance leaked counts: %d %v", rows, err)
	}
}
