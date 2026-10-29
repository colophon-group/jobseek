package main

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"testing"
	"time"
)

func TestMissingScheduleCanonicalScanAndInstalledCommand(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	dsn := os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires private PostgreSQL fixture")
	}
	base, e := url.Parse(dsn)
	if e != nil || (base.Hostname() != "localhost" && base.Hostname() != "127.0.0.1") {
		t.Fatal("requires private local PostgreSQL fixture")
	}
	admin, e := pgx.Connect(ctx, dsn)
	if e != nil {
		t.Fatal("fixture administrator unavailable")
	}
	db := fmt.Sprintf("missing_schedule_test_%d", time.Now().UnixNano())
	if _, e = admin.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{db}.Sanitize()); e != nil {
		admin.Close(ctx)
		t.Fatal("private fixture database unavailable")
	}
	t.Cleanup(func() {
		cleanup, stop := context.WithTimeout(context.Background(), 10*time.Second)
		defer stop()
		if _, err := admin.Exec(cleanup, "DROP DATABASE "+pgx.Identifier{db}.Sanitize()+" WITH (FORCE)"); err != nil {
			t.Error("private fixture cleanup failed")
		}
		admin.Close(cleanup)
	})
	base.Path = "/" + db
	dsn = base.String()
	conn, e := pgx.Connect(ctx, dsn)
	if e != nil {
		t.Fatal("private fixture connection unavailable")
	}
	defer conn.Close(ctx)
	if _, e = conn.Exec(ctx, "CREATE SEQUENCE public.lightpanda_b0_routing_epoch_seq;CREATE TABLE public.ordinary_worker_ownership_plan(state text)"); e != nil {
		t.Fatal(e)
	}
	schema := fmt.Sprintf("private_missing_%d", time.Now().UnixNano())
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, e = conn.Exec(ctx, "CREATE SCHEMA "+quoted); e != nil {
		t.Fatal(e)
	}
	defer conn.Exec(ctx, "DROP SCHEMA "+quoted+" CASCADE")
	if _, e = conn.Exec(ctx, "SET search_path TO "+quoted); e != nil {
		t.Fatal(e)
	}
	if _, e = conn.Exec(ctx, `CREATE TABLE job_board(id uuid PRIMARY KEY,crawler_type text,metadata jsonb,scraper_needs_browser bool,is_enabled bool,tdm_reserved bool,scrape_interval_hours integer); CREATE TABLE job_posting(id uuid PRIMARY KEY,source_url text,board_id uuid,description_r2_hash bigint,next_scrape_at timestamptz,is_active bool,leased_until timestamptz)`); e != nil {
		t.Fatal(e)
	}
	if _, e = conn.Exec(ctx, "SELECT setval('public.lightpanda_b0_routing_epoch_seq',1,true)"); e != nil {
		t.Fatal(e)
	}
	c := capacityRedisFixture(t, ctx)
	want := map[string]bool{}
	due := time.Unix(1234, 123456000).UTC()
	for i, mode := range []string{"missing", "missing-browser", "cache", "queued", "inflight", "deadletter", "inactive", "disabled", "reserved", "unscheduled", "leased", "no-scrape", "enriched-skip"} {
		id := fmt.Sprintf("00000000-0000-0000-0000-%012d", i+1)
		active, enabled, reserved, browser := mode != "inactive", mode != "disabled", mode == "reserved", mode == "missing-browser"
		kind, metadata := "dom", `{"scraper_type":"dom"}`
		if mode == "no-scrape" {
			kind = "greenhouse"
			metadata = `{}`
		}
		if mode == "enriched-skip" {
			metadata = `{"scraper_type":"skip","scraper_config":{"enrich":[]}}`
		}
		if _, e = conn.Exec(ctx, `INSERT INTO job_board VALUES($1::uuid,$2,$3::jsonb,$4,$5,$6,72)`, id, kind, metadata, browser, enabled, reserved); e != nil {
			t.Fatal(e)
		}
		var next, lease any = due, nil
		if mode == "unscheduled" {
			next = nil
		}
		if mode == "leased" {
			lease = time.Now().Add(time.Hour)
		}
		if _, e = conn.Exec(ctx, `INSERT INTO job_posting VALUES($1::uuid,'https://example.test/'||$1::text,$1::uuid,12345,$2,$3,$4)`, id, next, active, lease); e != nil {
			t.Fatal(e)
		}
		if e = c.HSet(ctx, "board:"+id, "scraper_needs_browser", map[bool]string{true: "1", false: "0"}[browser]).Err(); e != nil {
			t.Fatal(e)
		}
		switch mode {
		case "cache":
			e = c.HSet(ctx, "scrape:"+id, "scrape_step", "3").Err()
		case "queued":
			e = c.Do(ctx, "ZADD", "scrapes_simple:example.test", 12, id).Err()
		case "inflight":
			e = c.Do(ctx, "ZADD", "inflight:simple", 13, "scrape|example.test|"+id).Err()
		case "deadletter":
			e = c.Do(ctx, "ZADD", "deadletter:simple", 14, "scrape|example.test|"+id).Err()
		}
		if e != nil {
			t.Fatal(e)
		}
		if mode == "missing" || mode == "missing-browser" || mode == "enriched-skip" {
			want[id] = browser
		}
	}
	u, e := url.Parse(dsn)
	if e != nil {
		t.Fatal(e)
	}
	params := u.Query()
	params.Set("search_path", schema)
	u.RawQuery = params.Encode()
	pool, e := pgxpool.New(ctx, u.String())
	if e != nil {
		t.Fatal(e)
	}
	defer pool.Close()
	if _, e = missingScheduleAuthority(ctx, pool, c); e != nil {
		t.Fatal("default fixture ownership unavailable", e)
	}
	rows := []capacitySchedule{}
	o := capacityOptions{Action: "repair-missing", Limit: 2}
	for {
		page, e := missingSchedulePage(ctx, pool, o)
		if e != nil {
			t.Fatal(e)
		}
		rows = append(rows, page...)
		if len(page) < o.Limit {
			break
		}
		o.AfterID = page[len(page)-1].ID
	}
	if len(rows) != 7 {
		t.Fatal("canonical eligibility changed", len(rows))
	}
	missing, e := missingScheduleCandidates(ctx, c, rows)
	if e != nil {
		t.Fatal(e)
	}
	restored := 0
	for _, row := range missing {
		domain, score, cfg, e := capacityScheduleValues(row)
		if e != nil {
			t.Fatal(e)
		}
		worker := "simple"
		if row.Browser {
			worker = "browser"
		}
		args := []any{worker, domain, row.ID, strconv.FormatFloat(score, 'f', -1, 64), "scrape", "0", "0"}
		for _, field := range []string{"source_url", "board_id", "description_r2_hash", "scrape_step", "scrape_interval_hours"} {
			args = append(args, field, cfg[field])
		}
		before, e := snapshotReaperKeys(ctx, c)
		if e != nil {
			t.Fatal(e)
		}
		n, e := c.EvalRO(ctx, missingScheduleProgram(), []string{"inspect", "2"}, args...).Int()
		if e != nil {
			t.Fatal(e)
		}
		after, e := snapshotReaperKeys(ctx, c)
		if e != nil || !reflect.DeepEqual(before, after) {
			t.Fatal("inspection mutated Redis")
		}
		if _, ok := want[row.ID]; ok != (n == 1) {
			t.Fatal("missing-only eligibility changed")
		}
		n, e = c.Eval(ctx, missingScheduleProgram(), []string{"apply", "2"}, args...).Int()
		if e != nil {
			t.Fatal(e)
		}
		restored += n
	}
	if restored != len(want) {
		t.Fatal("missing candidates lost", restored)
	}
	for id, browser := range want {
		worker := "simple"
		if browser {
			worker = "browser"
		}
		if c.ZScore(ctx, "scrapes_"+worker+":example.test", id).Val() != 1234.123456 || c.HGet(ctx, "scrape:"+id, "scrape_interval_hours").Val() != "72" {
			t.Fatal("canonical deadline/interval changed")
		}
	}
	var drift int
	if e = conn.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE next_scrape_at IS NOT NULL AND next_scrape_at<>$1", due).Scan(&drift); e != nil || drift != 0 {
		t.Fatal("canonical deadlines mutated")
	}
	binary := filepath.Join(t.TempDir(), "native-missing-fixture")
	build := exec.CommandContext(ctx, "go", "build", "-o", binary, ".")
	if out, e := build.CombinedOutput(); e != nil {
		t.Fatalf("private CLI build failed: %v %s", e, out)
	}
	var restoreID string
	for id := range want {
		restoreID = id
		break
	}
	if e = c.Del(ctx, "scrape:"+restoreID).Err(); e != nil {
		t.Fatal(e)
	}
	for _, worker := range []string{"simple", "browser"} {
		if e = c.ZRem(ctx, "scrapes_"+worker+":example.test", restoreID).Err(); e != nil {
			t.Fatal(e)
		}
	}
	run := func(apply bool) map[string]any {
		t.Helper()
		args := []string{"--redis-capacity", "repair-missing", "--limit", "2"}
		if apply {
			args = append(args, "--apply")
		}
		cmd := exec.CommandContext(ctx, binary, args...)
		cmd.Env = append(os.Environ(), "LOCAL_DATABASE_URL="+u.String(), "REDIS_URL=unix://"+c.Options().Addr, "LIGHTPANDA_B0_PRODUCER_MODE=off")
		out, e := cmd.CombinedOutput()
		if e != nil {
			t.Fatal("installed missing-only CLI failed", e)
		}
		var report map[string]any
		if json.Unmarshal(out, &report) != nil {
			t.Fatal("invalid aggregate CLI report")
		}
		if report["complete"] != true || report["canonical_candidates_scanned"] != float64(7) {
			t.Fatal("incomplete canonical scan")
		}
		return report
	}
	before, e := snapshotReaperKeys(ctx, c)
	if e != nil {
		t.Fatal(e)
	}
	dry := run(false)
	after, e := snapshotReaperKeys(ctx, c)
	if e != nil || !reflect.DeepEqual(before, after) || dry["reconstructed_or_would_reconstruct"] != float64(1) {
		t.Fatal("CLI dry run changed lifecycle")
	}
	applied := run(true)
	if applied["reconstructed_or_would_reconstruct"] != float64(1) {
		t.Fatal("CLI did not restore exact absence")
	}
	before, e = snapshotReaperKeys(ctx, c)
	if e != nil {
		t.Fatal(e)
	}
	again := run(true)
	after, e = snapshotReaperKeys(ctx, c)
	if e != nil || !reflect.DeepEqual(before, after) || again["reconstructed_or_would_reconstruct"] != float64(0) {
		t.Fatal("CLI repeat changed existing state")
	}

}
