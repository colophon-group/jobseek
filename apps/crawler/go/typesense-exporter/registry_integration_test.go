package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func registryTestDatabase(t *testing.T) (context.Context, *pgx.Conn, *pgx.Conn) {
	t.Helper()
	dsn := os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("GO_TYPESENSE_TEST_DATABASE_URL is not set")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
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
	schema := pgx.Identifier{fmt.Sprintf("registry_test_%d", time.Now().UnixNano())}.Sanitize()
	if _, err = conn.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = conn.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE") })
	for _, db := range []*pgx.Conn{conn, observer} {
		if _, err = db.Exec(ctx, "SET search_path TO "+schema); err != nil {
			t.Fatal(err)
		}
	}
	body, err := os.ReadFile("testdata/registry_schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var statements []string
	if err = json.Unmarshal(body, &statements); err != nil {
		t.Fatal(err)
	}
	for _, sql := range statements {
		if _, err = conn.Exec(ctx, sql); err != nil {
			t.Fatalf("schema: %v", err)
		}
	}
	return ctx, conn, observer
}

func TestRegistryPostgresTaxonomyCompanyIdentityAndRollback(t *testing.T) {
	ctx, conn, observer := registryTestDatabase(t)
	body, err := os.ReadFile("testdata/registry_fixture.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixtures []struct {
		CSV map[string]string `json:"csv"`
	}
	if err = json.Unmarshal(body, &fixtures); err != nil {
		t.Fatal(err)
	}
	tables := map[string]registryTable{}
	for name, raw := range fixtures[0].CSV {
		table, err := parseRegistryCSV([]byte(raw))
		if err != nil {
			t.Fatal(err)
		}
		tables[name] = table
	}
	plan, err := registryTaxonomyPlan(tables)
	if err != nil {
		t.Fatal(err)
	}
	companies, err := registryCompanyPlan(tables["companies"], tables["company_descriptions"])
	if err != nil {
		t.Fatal(err)
	}
	plan = append(plan, companies...)
	tx, err := conn.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if err = executeRegistryPlan(ctx, tx, plan); err != nil {
		t.Fatal(err)
	}
	var count int
	if err = observer.QueryRow(ctx, "SELECT count(*) FROM company").Scan(&count); err != nil || count != 0 {
		t.Fatalf("uncommitted registry became visible: %d %v", count, err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	var before, after string
	snapshot := `SELECT coalesce(jsonb_agg(jsonb_build_array(slug,id) ORDER BY slug),'[]')::text FROM company`
	if err = conn.QueryRow(ctx, snapshot).Scan(&before); err != nil {
		t.Fatal(err)
	}
	tx, err = conn.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if err = executeRegistryPlan(ctx, tx, plan); err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if err = conn.QueryRow(ctx, snapshot).Scan(&after); err != nil || before != after {
		t.Fatalf("company IDs changed: %s -> %s (%v)", before, after, err)
	}
	tx, err = conn.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "UPDATE company SET name='uncommitted'"); err != nil {
		t.Fatal(err)
	}
	bad := registryPlan{{Key: "_UPSERT_COMPANIES", Args: []any{[]string{"broken"}, []*string{nil}, []*string{nil}, []*string{nil}, []*string{nil}, []*string{nil}, []*int64{nil}, []*int64{nil}, []*int64{nil}, []*string{nil}}}}
	if err = executeRegistryPlan(ctx, tx, bad); err == nil {
		t.Fatal("null company name succeeded")
	}
	if err = tx.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	if err = conn.QueryRow(ctx, "SELECT count(*) FROM company WHERE name='uncommitted' OR slug='broken'").Scan(&count); err != nil || count != 0 {
		t.Fatalf("failed transaction leaked: %d %v", count, err)
	}
}

func TestRegistryPostgresBoardIdentityRecoveryRehomeAndRollback(t *testing.T) {
	ctx, conn, observer := registryTestDatabase(t)
	_, err := conn.Exec(ctx, "INSERT INTO company(slug,name) VALUES ('first','First'),('second','Second')")
	if err != nil {
		t.Fatal(err)
	}
	table, err := parseRegistryCSV([]byte("company_slug,board_slug,board_url,monitor_type,monitor_config\nfirst,one,https://first.invalid/jobs,rss,{}\nsecond,two,https://second.invalid/jobs,rss,{}\n"))
	if err != nil {
		t.Fatal(err)
	}
	boards, err := prepareRegistryBoards(table)
	if err != nil {
		t.Fatal(err)
	}
	clock := func() time.Time { return time.Now() }
	run := func(input []registryBoard, commit bool) (boardSyncInput, error) {
		tx, err := conn.Begin(ctx)
		if err != nil {
			return boardSyncInput{}, err
		}
		defer tx.Rollback(ctx)
		effects, err := stageRegistryBoards(ctx, tx, input, clock)
		if err != nil {
			return effects, err
		}
		if commit {
			err = tx.Commit(ctx)
		}
		return effects, err
	}
	tx, err := conn.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	effects, err := stageRegistryBoards(ctx, tx, boards, clock)
	if err != nil {
		t.Fatal(err)
	}
	var count int
	if err = observer.QueryRow(ctx, "SELECT count(*) FROM job_board").Scan(&count); err != nil || count != 0 {
		t.Fatalf("board write escaped transaction: %d %v", count, err)
	}
	if len(effects.Schedules) != 2 {
		t.Fatalf("missing committed candidates: %+v", effects)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	id := effects.Schedules[0].BoardID
	_, err = conn.Exec(ctx, `UPDATE job_board SET board_status='quarantined',next_check_at='2050-01-01T00:00:00Z',lease_owner='owner',leased_until='2050-01-02T00:00:00Z',metadata=metadata || '{"recent_discovered_counts":[10,12],"pcsx_watermark":{"max_ts":123},"_identity_migration_receipt":{"completed":true}}'::jsonb WHERE id=$1::uuid`, id)
	if err != nil {
		t.Fatal(err)
	}
	_, err = conn.Exec(ctx, `INSERT INTO job_posting(company_id,board_id,source_url) SELECT company_id,id,'https://first.invalid/job/1' FROM job_board WHERE id=$1::uuid`, id)
	if err != nil {
		t.Fatal(err)
	}
	effects, err = run(boards, true)
	if err != nil {
		t.Fatal(err)
	}
	first := effects.Schedules[0]
	if first.BoardID != id || first.FirstTime || first.NextCheckAt != 2524608000 {
		t.Fatalf("recovery deadline/identity lost: %+v", first)
	}
	var cached map[string]any
	if err = json.Unmarshal([]byte(first.Config["metadata"]), &cached); err != nil || cached["pcsx_watermark"] == nil || cached["recent_discovered_counts"] == nil {
		t.Fatalf("runtime metadata lost: %s %v", first.Config["metadata"], err)
	}
	changed := append([]registryBoard{}, boards...)
	changed[0].Company = "second"
	changed[0].URL = "https://replacement.invalid/jobs"
	effects, err = run(changed[:1], true)
	if err != nil {
		t.Fatal(err)
	}
	if effects.Schedules[0].BoardID != id || len(effects.Orphans) != 1 {
		t.Fatalf("rename lost identity or removal: %+v", effects)
	}
	var mismatch int
	if err = conn.QueryRow(ctx, `SELECT count(*) FROM job_posting p JOIN job_board b ON b.id=p.board_id WHERE p.company_id<>b.company_id`).Scan(&mismatch); err != nil || mismatch != 0 {
		t.Fatalf("posting ownership not rehomed: %d %v", mismatch, err)
	}
	if err = json.Unmarshal([]byte(effects.Schedules[0].Config["metadata"]), &cached); err != nil {
		t.Fatal(err)
	}
	// Decode into a fresh map: encoding/json retains absent keys in reused maps.
	cached = map[string]any{}
	_ = json.Unmarshal([]byte(effects.Schedules[0].Config["metadata"]), &cached)
	if cached["_identity_migration_receipt"] == nil || cached["pcsx_watermark"] != nil {
		t.Fatalf("rename receipt/runtime state wrong: %+v", cached)
	}
	bad := append([]registryBoard{}, changed...)
	bad[0].URL = "https://uncommitted.invalid/jobs"
	bad[1].Company = "missing"
	if _, err = run(bad, true); err == nil {
		t.Fatal("missing company accepted")
	}
	var actualURL string
	if err = conn.QueryRow(ctx, "SELECT board_url FROM job_board WHERE id=$1::uuid", id).Scan(&actualURL); err != nil || actualURL != changed[0].URL {
		t.Fatalf("partial rehome survived rollback: %s %v", actualURL, err)
	}
	// Reappearing removed sources enter quarantine and use recurring scheduling.
	effects, err = run(changed, true)
	if err != nil {
		t.Fatal(err)
	}
	if effects.Schedules[1].FirstTime {
		t.Fatal("removed source reappeared as unguarded first-time work")
	}
	if _, err = run(nil, true); err != nil {
		t.Fatal(err)
	}
	if err = conn.QueryRow(ctx, "SELECT count(*) FROM job_board WHERE is_enabled").Scan(&count); err != nil || count != 2 {
		t.Fatalf("empty input retired live boards: %d %v", count, err)
	}
}

func TestRegistryRedisMetadataPreservesPythonEncoding(t *testing.T) {
	got, err := registryRedisMetadata([]byte(`{"z": "ä🚀", "a": [1.0, 0.00000001, null, true], "object": {"x": "<"}}`))
	want := `{"z": "\u00e4\ud83d\ude80", "a": [1.0, 1e-08, null, true], "object": {"x": "<"}}`
	if err != nil || got != want {
		t.Fatalf("%s != %s (%v)", got, want, err)
	}
}
