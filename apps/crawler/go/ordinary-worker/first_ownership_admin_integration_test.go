package worker

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealFirstOwnershipExecutableActivatesAndRetires(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx := context.Background()
	e := newNativeExecutableFixture(t, f, f.dsn)
	// Only this isolated loopback test database discards its synthetic owner.
	if _, err := f.pg.Exec(ctx, "TRUNCATE public.ordinary_worker_ownership_plan CASCADE"); err != nil {
		t.Fatal(err)
	}
	if err := f.r.Del(ctx, "ordinary:ownership:active").Err(); err != nil {
		t.Fatal(err)
	}
	plan, err := f.a.StageGreenhouseOwnership(ctx, ordinaryFixtureSourceRevision(t), []string{f.board})
	if err != nil {
		t.Fatal(err)
	}
	id := fixtureID(t)
	_, err = f.pg.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser)
 VALUES($1::uuid,$2::uuid,'browser-use-careers','https://jobs.example.test/careers','api_sniffer','{}',60,24,'',false,true)`, id, f.company)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = f.pg.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", id) })
	config := map[string]string{"board_slug": "browser-use-careers", "board_url": "https://jobs.example.test/careers", "crawler_type": "api_sniffer", "company_id": f.company, "metadata": "{}", "check_interval_minutes": "60", "scrape_interval_hours": "24", "throttle_key": "", "domain": "jobs.example.test", "monitor_needs_browser": "0", "scraper_needs_browser": "1"}
	if err := f.r.HSet(ctx, "board:"+id, config).Err(); err != nil {
		t.Fatal(err)
	}
	lua, err := os.ReadFile("../../src/lua/lightpanda_b0_queue.lua")
	if err != nil {
		t.Fatal(err)
	}
	keys := []string{}
	for _, suffix := range []string{"route", "records", "ready", "inflight", "dead", "terminal", "origin-holders"} {
		keys = append(keys, "lightpanda-b0:{production-b0}:"+suffix)
	}
	args := []any{"initialize_producer", "lightpanda-b0", strconv.FormatInt(plan.Epoch(), 10), "go", "", "0", "", "0", "0", "0", "", "", "", "64", "2.0", "", "0", "production-b0", "", "0", "c1", 1, "0", "browser-use-careers"}
	result, err := f.r.Eval(ctx, string(lua), keys, args...).Slice()
	if err != nil || len(result) != 12 || result[0] != "accepted" {
		t.Fatal("real B0 initialization", err)
	}
	r := FirstOwnershipRequest{Version: "jobseek.ordinary.first-owner-request/v1", Operation: "activate", SourceRevision: plan.SourceRevision(), RoutingEpoch: plan.Epoch(), PlanSHA256: plan.SHA256(), ProjectionSHA1: plan.ProjectionSHA1(), CrawlerImageRef: "ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("d", 64), B0ReceiptSHA256: strings.Repeat("e", 64), B0Cohort: "c1", Namespace: "production-b0", ShardID: "lightpanda-b0", ColdHostSHA256: strings.Repeat("f", 64)}
	path := filepath.Join(e.directory, "first-owner.json")
	call := func(accepted bool) {
		t.Helper()
		body, _ := json.Marshal(r)
		if err := os.WriteFile(path, append(body, '\n'), 0o600); err != nil {
			t.Fatal(err)
		}
		command := exec.Command(e.binary, "--"+r.Operation+"-first-ownership")
		command.Env = []string{"PATH=" + os.Getenv("PATH"), "LOCAL_DATABASE_URL=" + f.dsn, "REDIS_URL=unix://" + f.r.Options().Addr, "ORDINARY_GO_WORKER_MODE=" + r.Operation + "-first-ownership", "ORDINARY_FIRST_OWNERSHIP_REQUEST_FILE=" + path, "ORDINARY_OWNERSHIP_SOURCE_REVISION=" + r.SourceRevision, "ORDINARY_OWNERSHIP_PLAN_SHA256=" + r.PlanSHA256, "ORDINARY_OWNERSHIP_PROJECTION_SHA1=" + r.ProjectionSHA1, "ORDINARY_OWNERSHIP_ROUTING_EPOCH=" + strconv.FormatInt(r.RoutingEpoch, 10), "CRAWLER_IMAGE_REF=" + r.CrawlerImageRef, "LIGHTPANDA_B0_ROUTING_EPOCH=" + strconv.FormatInt(r.RoutingEpoch, 10), "LIGHTPANDA_B0_QUEUE_NAMESPACE=" + r.Namespace, "LIGHTPANDA_B0_SHARD_ID=" + r.ShardID, "LIGHTPANDA_B0_PRODUCER_COHORT=" + r.B0Cohort}
		output, err := command.CombinedOutput()
		if !accepted {
			if err == nil || strings.Contains(string(output), f.dsn) {
				t.Fatal("invalid cold request accepted or exposed")
			}
			return
		}
		var got queue.FirstOwnershipResult
		state := "active"
		if r.Operation == "retire" {
			state = "retired"
		}
		if err != nil || json.Unmarshal(output, &got) != nil || got.State != state || got.PlanSHA256 != plan.SHA256() || got.RoutingEpoch != plan.Epoch() {
			t.Fatal("cold executable lost exact ownership result", err)
		}
	}
	// A wrong startup projection is refused BEFORE first publication.
	r.ProjectionSHA1 = strings.Repeat("f", 40)
	call(false)
	if f.r.Exists(ctx, "ordinary:ownership:active").Val() != 0 {
		t.Fatal("wrong startup identity published")
	}
	r.ProjectionSHA1 = plan.ProjectionSHA1()
	if err := f.r.Do(ctx, "ACL", "SETUSER", "default", "-save").Err(); err != nil {
		t.Fatal(err)
	}
	call(false)
	var state string
	if err := f.pg.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", plan.SHA256()).Scan(&state); err != nil || state != "staged" {
		t.Fatal("unacknowledged SAVE activated SQL")
	}
	if err := f.r.Do(ctx, "ACL", "SETUSER", "default", "+save").Err(); err != nil {
		t.Fatal(err)
	}
	call(true)
	r.Operation = "retire"
	call(true)
	if f.r.Exists(ctx, "ordinary:ownership:active").Val() != 0 {
		t.Fatal("retired executable left an owner")
	}
	if err := f.r.Do(ctx, "ACL", "SETUSER", "default", "-save").Err(); err != nil {
		t.Fatal(err)
	}
	call(true)
}
