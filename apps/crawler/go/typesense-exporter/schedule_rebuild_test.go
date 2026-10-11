package main

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/jackc/pgx/v5"
)

func TestCapacityRebuildBoundsAndProducerDecisions(t *testing.T) {
	for _, args := range [][]string{{"rebuild", "--limit=0"}, {"rebuild", "--limit=50001"}, {"rebuild", "--after-id=invalid"}} {
		if _, err := parseCapacityOptions(args); err == nil {
			t.Fatal("accepted invalid resumable slice", args)
		}
	}
	for score, want := range map[float64]int64{0: 0, 1.0000001: 1001, 1234.0001: 1234001, 1234.001: 1234001, 9_999_999_999.999: 9_999_999_999_999} {
		if got, err := capacityProducerMillis(score); err != nil || got != want {
			t.Fatal("original decimal ceiling differs", score, got, want, err)
		}
	}
	for _, score := range []float64{-1, math.NaN(), math.Inf(1), 10_000_000_000} {
		if _, err := capacityProducerMillis(score); err == nil {
			t.Fatal("accepted invalid producer schedule")
		}
	}
	d := capacityProducerDecision{Version: capacityProducerProtocol, Outcome: "legacy", Reason: "literal_legacy", BoardSlugs: []string{}, LifetimeCapacity: 2048, LifetimeHeadroom: 2048}
	encode := func() []byte {
		b, err := b0task.CanonicalJSON(d, true)
		if err != nil {
			t.Fatal(err)
		}
		return b
	}
	if owned, added, err := capacityProducerDecode(encode()); err != nil || owned || added {
		t.Fatal("literal legacy changed", err)
	}
	d.Outcome, d.Reason = "activated", "activated"
	d.Activated, d.PreparationDigest, d.PayloadSHA256 = true, strings.Repeat("a", 64), strings.Repeat("b", 64)
	if owned, added, err := capacityProducerDecode(encode()); err != nil || !owned || !added {
		t.Fatal("activation changed", err)
	}
	d.Reason, d.Activated = "already_activated", false
	if owned, added, err := capacityProducerDecode(encode()); err != nil || !owned || added {
		t.Fatal("idempotent activation changed", err)
	}
	valid := encode()
	for _, malformed := range [][]byte{append(valid, ' '), []byte(strings.Replace(string(valid), `"activated":false`, `"activated":null`, 1)), []byte(strings.Replace(string(valid), `"outcome":"activated"`, `"outcome":"prepared"`, 1)), []byte(strings.Replace(string(valid), `"lifetime_headroom":2048`, `"lifetime_headroom":1`, 1)), []byte(strings.Replace(string(valid), `"version":`, `"extra":0,"version":`, 1))} {
		if _, _, err := capacityProducerDecode(malformed); err == nil {
			t.Fatal("malformed or unauthenticated decision authorized enqueue")
		}
	}
}

func TestCapacityRebuildRealRedisLanesAndDurableGuard(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	c := capacityRedisFixture(t, ctx)
	directory := t.TempDir()
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	p := capacityProducer{mode: "off", socket: filepath.Join(directory, "control.sock"), uid: uint32(os.Getuid())}
	hash := "hash"
	rows := []capacitySchedule{{ID: "first", URL: "https://Careers.Example/job/1", Board: "board", Due: time.Unix(2000, 0)}, {ID: "recurring", URL: "https://jobs.csod.com/job/2", Board: "board", Hash: &hash, Due: time.Unix(1234, 123456000), Browser: true}}
	o := capacityOptions{Action: "rebuild", Limit: 2}
	r, err := capacityRebuildSchedules(ctx, c, rows, o, p, deadletterDelays{Default: 2, ATS: .5})
	if err != nil || r.Selected != 2 || r.Complete || r.NewlyEnqueued != 0 || r.AlreadyPresent != 0 || c.DBSize(ctx).Val() != 0 {
		t.Fatal("dry run or cursor changed", r, err)
	}
	o.Apply = true
	r, err = capacityRebuildSchedules(ctx, c, rows, o, p, deadletterDelays{Default: 2, ATS: .5})
	if err != nil || r.NewlyEnqueued != 2 || r.AlreadyPresent != 0 {
		t.Fatal(r, err)
	}
	for _, tc := range []struct {
		key, id string
		due     float64
	}{{"ft_scrapes_simple:careers.example", "first", 0}, {"scrapes_browser:jobs.csod.com", "recurring", 1234.123456}} {
		if due, err := c.ZScore(ctx, tc.key, tc.id).Result(); err != nil || due != tc.due {
			t.Fatal("original schedule lane/score differs", due, err)
		}
		if c.HGet(ctx, "scrape:"+tc.id, "scrape_step").Val() != "0" {
			t.Fatal("original fallback reset changed")
		}
	}
	if c.Get(ctx, "delay:careers.example").Val() != "2" || c.Get(ctx, "delay:jobs.csod.com").Val() != "0.5" {
		t.Fatal("original throttle changed")
	}
	r, err = capacityRebuildSchedules(ctx, c, rows, o, p, deadletterDelays{Default: 2, ATS: .5})
	if err != nil || r.NewlyEnqueued != 0 || r.AlreadyPresent != 2 {
		t.Fatal("idempotency differs", r, err)
	}
	if err := os.WriteFile(filepath.Join(directory, ".activation-v1"), []byte("authority"), 0600); err != nil {
		t.Fatal(err)
	}
	before := c.DBSize(ctx).Val()
	if _, err := capacityRebuildSchedules(ctx, c, []capacitySchedule{{ID: "refused", URL: "https://refused.example/1"}}, o, p, deadletterDelays{Default: 2, ATS: .5}); err == nil {
		t.Fatal("durable producer marker bypassed")
	}
	if c.DBSize(ctx).Val() != before || c.Exists(ctx, "scrape:refused").Val() != 0 {
		t.Fatal("guard refusal mutated legacy Redis")
	}
}

func TestCapacityRebuildPostgresRedisInstalledCommand(t *testing.T) {
	dsn := os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("GO_TYPESENSE_TEST_DATABASE_URL is not set")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	c := capacityRedisFixture(t, ctx)
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(context.Background())
	schema := fmt.Sprintf("schedule_rebuild_test_%d", time.Now().UnixNano())
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, err := conn.Exec(ctx, "CREATE SCHEMA "+quoted); err != nil {
		t.Fatal(err)
	}
	defer conn.Exec(context.Background(), "DROP SCHEMA "+quoted+" CASCADE")
	if _, err := conn.Exec(ctx, "SET search_path TO "+quoted); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Exec(ctx, `CREATE TABLE job_board(id uuid PRIMARY KEY,crawler_type text,metadata jsonb,scraper_needs_browser bool,is_enabled bool); CREATE TABLE job_posting(id uuid PRIMARY KEY,source_url text,board_id uuid,description_r2_hash text,next_scrape_at timestamptz,is_active bool)`); err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		kind, metadata string
		selected       bool
	}{
		{"dom", `{"scraper_type":"dom"}`, true}, {"dom", `{"scraper_type":"skip"}`, false}, {"dom", `{"scraper_type":"skip","scraper_config":{"enrich":[]}}`, true},
		{"greenhouse", `{}`, false}, {"rss", `{}`, false}, {"rss", `{"variant":"legacy","scraper_type":"dom"}`, true}, {"rss", `{"variant":"legacy"}`, false}, {"api_sniffer", `{"fields":{}}`, false}, {"api_sniffer", `{"scraper_type":"dom"}`, true}, {"api_sniffer", `{}`, false},
		{"nextdata", `{"fields":{}}`, false}, {"smartrecruiters", `{"canonical_job_id_url_template":"https://jobs.example/{jobId}"}`, false}, {"smartrecruiters", `{"canonical_job_id_url_template":""}`, false},
	}
	selected := 0
	for i, tc := range cases {
		id := fmt.Sprintf("00000000-0000-0000-0000-%012d", i+1)
		if _, err := conn.Exec(ctx, `INSERT INTO job_board VALUES($1::uuid,$2,$3::jsonb,false,false)`, id, tc.kind, tc.metadata); err != nil {
			t.Fatal(err)
		}
		if _, err := conn.Exec(ctx, `INSERT INTO job_posting VALUES($1::uuid,'https://jobs.example/'||$1::text,$1::uuid,NULL,'2026-01-01T00:00:00Z',true)`, id); err != nil {
			t.Fatal(err)
		}
		if tc.selected {
			selected++
		}
	}
	// Inactive and unscheduled postings never enter a rebuild slice.
	if _, err := conn.Exec(ctx, `INSERT INTO job_posting VALUES('00000000-0000-0000-0000-000000000099','https://jobs.example/inactive','00000000-0000-0000-0000-000000000001',NULL,'2026-01-01T00:00:00Z',false),('00000000-0000-0000-0000-000000000098','https://jobs.example/unscheduled','00000000-0000-0000-0000-000000000001',NULL,NULL,true)`); err != nil {
		t.Fatal(err)
	}
	binary := filepath.Join(t.TempDir(), "native-exporter")
	build := exec.CommandContext(ctx, "go", "build", "-o", binary, ".")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build installed command: %v %s", err, output)
	}
	databaseURL, err := url.Parse(dsn)
	if err != nil {
		t.Fatal(err)
	}
	params := databaseURL.Query()
	params.Set("search_path", schema)
	databaseURL.RawQuery = params.Encode()
	run := func(args ...string) capacityRebuildReport {
		command := exec.CommandContext(ctx, binary, append([]string{"--redis-capacity", "rebuild"}, args...)...)
		command.Env = append(os.Environ(), "LOCAL_DATABASE_URL="+databaseURL.String(), "PGOPTIONS=-c search_path="+schema, "REDIS_URL=unix://"+c.Options().Addr, "LIGHTPANDA_B0_PRODUCER_MODE=off")
		output, err := command.CombinedOutput()
		if err != nil {
			t.Fatalf("installed rebuild: %v %s", err, output)
		}
		var r capacityRebuildReport
		if json.Unmarshal(output, &r) != nil {
			t.Fatal("invalid machine report", string(output))
		}
		return r
	}
	dry := run()
	if dry.Selected != selected || !dry.DryRun || !dry.Complete || c.DBSize(ctx).Val() != 0 {
		t.Fatal("canonical no-scrape selection changed", dry, selected)
	}
	applied := run("--apply")
	if applied.Selected != selected || applied.NewlyEnqueued != selected || applied.AlreadyPresent != 0 {
		t.Fatal("installed enqueue changed", applied)
	}
	repeated := run("--apply")
	if repeated.NewlyEnqueued != 0 || repeated.AlreadyPresent != selected {
		t.Fatal("installed idempotency changed", repeated)
	}
	page := run("--limit=1")
	if page.Selected != 1 || page.Complete || page.NextAfterID == nil {
		t.Fatal("resumable page changed", page)
	}
	next := run("--after-id=" + *page.NextAfterID)
	if next.Selected != selected-1 || next.AfterID == nil || *next.AfterID != *page.NextAfterID {
		t.Fatal("UUID cursor changed", next)
	}
	var total int
	if conn.QueryRow(ctx, "SELECT count(*) FROM job_posting").Scan(&total) != nil || total != len(cases)+2 {
		t.Fatal("canonical rows changed")
	}
	keys, _ := c.Keys(ctx, "scrape:*").Result()
	if len(keys) != selected {
		t.Fatal("skip/inactive/unscheduled row enqueued", keys)
	}
	for _, key := range keys {
		values, _ := c.HGetAll(ctx, key).Result()
		if values["board_id"] != strings.TrimPrefix(key, "scrape:") || values["scrape_step"] != "0" || values["description_r2_hash"] != "" {
			t.Fatal("original canonical config differs", values)
		}
	}
}
