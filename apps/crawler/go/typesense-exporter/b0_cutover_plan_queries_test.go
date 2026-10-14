package main

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func TestB0CutoverPlanningQueriesBindActualOriginal(t *testing.T) {
	body, err := os.ReadFile("../../src/lightpanda/activation.py")
	if err != nil {
		t.Fatal("original planning source unavailable")
	}
	for _, pair := range [][2]string{{"_BOARDS_SQL", b0PlanBoardsSQL}, {"_POSTINGS_SQL", b0PlanActivationPostingsSQL}, {"_ROLLBACK_RECORDS_SQL", b0PlanRollbackPostingsSQL}, {"_GO_FENCE_IDS_SQL", b0PlanFenceIDsSQL}} {
		_, tail, ok := strings.Cut(string(body), pair[0]+" = \"\"\"")
		if !ok {
			t.Fatal("original query unavailable", pair[0])
		}
		query, _, ok := strings.Cut(tail, "\"\"\"")
		if !ok || query != pair[1] {
			t.Fatal("original query contract differs", pair[0])
		}
	}
}

func TestRealB0CutoverPlanReadsCanonicalRows(t *testing.T) {
	dsn := os.Getenv("JOBSEEK_ORDINARY_QUEUE_TEST_DATABASE_URL")
	if dsn == "" {
		if os.Getenv("JOBSEEK_ORDINARY_QUEUE_REQUIRE_POSTGRES") == "1" {
			t.Fatal("private PG fixture required")
		}
		t.Skip("requires private canonical fixture")
	}
	u, err := url.Parse(dsn)
	if err != nil || !(u.Hostname() == "localhost" || u.Hostname() == "127.0.0.1") || !strings.HasSuffix(u.Path, "_ordinary_worker_test") {
		t.Fatal("requires private canonical fixture")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	observer, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal("private observer unavailable")
	}
	t.Cleanup(observer.Close)
	schema := fmt.Sprintf("b0_plan_%d", time.Now().UnixNano())
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, err = observer.Exec(ctx, "CREATE SCHEMA "+quoted); err != nil {
		t.Fatal("private schema unavailable")
	}
	t.Cleanup(func() { _, _ = observer.Exec(context.Background(), "DROP SCHEMA "+quoted+" CASCADE") })
	ddl := `CREATE TABLE ` + quoted + `.job_board(id uuid,board_slug text,board_url text,is_enabled boolean,board_status text,metadata jsonb,crawler_type text,scraper_needs_browser boolean,scrape_interval_hours integer);
 CREATE TABLE ` + quoted + `.job_posting(id uuid,board_id uuid,source_url text,description_r2_hash bigint,is_active boolean,next_scrape_at timestamptz,leased_until timestamptz);
 CREATE TABLE ` + quoted + `.lightpanda_b0_write_fence(job_posting_id uuid,engine_owner text,shard_id text,routing_epoch bigint);`
	if _, err = observer.Exec(ctx, ddl); err != nil {
		t.Fatal("private relations unavailable")
	}
	params := u.Query()
	params.Set("search_path", schema)
	u.RawQuery = params.Encode()
	config, err := providerCutoverPoolConfig(u.String(), "lightpanda-b0-activation")
	if err != nil {
		t.Fatal("private pool config failed")
	}
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal("private canonical connection unavailable")
	}
	defer pool.Close()
	board := "11111111-1111-4111-8111-111111111111"
	posting := "00000000-0000-4000-8000-000000000001"
	if _, err = pool.Exec(ctx, `INSERT INTO job_board VALUES($1,'browser-use-careers','https://jobs.example',true,'active','{"scraper_type":"json-ld"}','json-ld',true,24)`, board); err != nil {
		t.Fatal("private board seed failed")
	}
	scheduled := time.Date(2026, 10, 14, 0, 0, 0, 123456000, time.UTC)
	if _, err = pool.Exec(ctx, `INSERT INTO job_posting VALUES($1,$2,'https://jobs.example/job',NULL,true,$3,NULL)`, posting, board, scheduled); err != nil {
		t.Fatal("private posting seed failed")
	}
	boards, err := b0PlanReadBoards(ctx, pool, []string{"browser-use-careers"})
	if err != nil || len(boards) != 1 || boards[0].ID != board || boards[0].Enabled == nil || !*boards[0].Enabled || boards[0].Browser == nil || !*boards[0].Browser || boards[0].Interval == nil || *boards[0].Interval != 24 {
		t.Fatal("canonical board read differs")
	}
	for _, rollback := range []bool{false, true} {
		ids := []string{"browser-use-careers"}
		if rollback {
			ids = []string{posting}
		}
		rows, err := b0PlanReadPostings(ctx, pool, ids, rollback)
		if err != nil || len(rows) != 1 || rows[0].ID != posting || rows[0].BoardID != board || rows[0].Hash != nil || rows[0].LeaseActive != nil || rows[0].Next == nil || !rows[0].Next.Equal(scheduled) || rows[0].Active == nil || !*rows[0].Active {
			t.Fatal("nullable canonical posting/schedule read differs")
		}
		if rollback && (rows[0].Enabled == nil || !*rows[0].Enabled || rows[0].Status != "active") {
			t.Fatal("rollback eligibility read differs")
		}
	}
	if _, err = pool.Exec(ctx, `INSERT INTO lightpanda_b0_write_fence VALUES($1,'go','lightpanda-b0',7),($1,'go','other',7)`, posting); err != nil {
		t.Fatal("private fence seed failed")
	}
	ids, err := b0PlanReadFenceIDs(ctx, pool, b0CutoverIdentity{Shard: "lightpanda-b0", Epoch: 7})
	if err != nil || len(ids) != 1 || ids[0] != posting {
		t.Fatal("route-scoped canonical fence read differs")
	}
}
