package worker

import (
	"context"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func firstExecutableOwnershipFixture(t *testing.T) (nativePipelineFixture, nativeExecutableFixture, *queue.OwnershipPlan) {
	t.Helper()
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
	return f, e, plan
}

func TestRealFirstOwnershipExecutableActivatesAndRetires(t *testing.T) {
	f, e, plan := firstExecutableOwnershipFixture(t)
	ctx := context.Background()
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
	// Production shutdown can retain SQL leases even though every writer is
	// stopped. The installed command must wait for database-clock expiry and
	// preserve the lease columns, rather than falsely completing or clearing it.
	var leaseUntil time.Time
	if err := f.pg.QueryRow(ctx, `UPDATE job_board
 SET lease_owner='cold-legacy-fixture',leased_until=clock_timestamp()+interval '2 seconds'
 WHERE id=$1::uuid RETURNING leased_until`, f.board).Scan(&leaseUntil); err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	call(true)
	var retainedLease time.Time
	var leaseOwner string
	if err := f.pg.QueryRow(ctx, "SELECT leased_until,lease_owner FROM job_board WHERE id=$1::uuid", f.board).Scan(&retainedLease, &leaseOwner); err != nil || !retainedLease.Equal(leaseUntil) || leaseOwner != "cold-legacy-fixture" || time.Since(started) < time.Second {
		t.Fatal("installed cold command did not preserve and await legacy lease expiry", err)
	}
	// The installed command must retire a claimed monitor whose process never
	// reached ACK. No worker restart or origin fetch is part of this recovery.
	native, err := queue.OpenOwnedAuthority(ctx, f.dsn, f.client, plan.Epoch(), plan.SHA256(), plan.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	defer native.Close()
	claim, err := native.Claim(ctx, queue.Simple)
	if err != nil || claim == nil || claim.Descriptor().ID != f.board {
		t.Fatal("installed admin interrupted-claim fixture", err)
	}
	var due time.Time
	var before string
	if err := f.pg.QueryRow(ctx, "SELECT next_check_at FROM job_board WHERE id=$1::uuid", f.board).Scan(&due); err != nil {
		t.Fatal(err)
	}
	if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(f)::text FROM ordinary_worker_write_fence f WHERE task_id=$1::uuid", f.board).Scan(&before); err != nil {
		t.Fatal(err)
	}
	r.Operation = "retire"
	call(true)
	var after string
	if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(f)::text FROM ordinary_worker_write_fence f WHERE task_id=$1::uuid", f.board).Scan(&after); err != nil || after != before {
		t.Fatal("installed retirement rewrote the interrupted receipt", err)
	}
	score, err := f.r.ZScore(ctx, "monitors_simple:greenhouse", f.board).Result()
	if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 || f.r.HLen(ctx, "inflight_tokens:simple").Val() != 0 {
		t.Fatal("installed retirement did not restore the canonical monitor", err)
	}
	if f.r.Exists(ctx, "ordinary:ownership:active").Val() != 0 {
		t.Fatal("retired executable left an owner")
	}
	if err := f.r.Do(ctx, "ACL", "SETUSER", "default", "-save").Err(); err != nil {
		t.Fatal(err)
	}
	call(true)
}

func TestRealFirstOwnershipExecutableCompatibilityRetirement(t *testing.T) {
	for _, format := range []string{"compact", "legacy-full"} {
		t.Run(format, func(t *testing.T) {
			f, e, installedPlan := firstExecutableOwnershipFixture(t)
			ctx := context.Background()
			oldSource := strings.Repeat("b", 40)
			if oldSource == installedPlan.SourceRevision() {
				t.Fatal("fixture requires distinct source revisions")
			}
			plan, err := f.a.StageGreenhouseOwnership(ctx, oldSource, []string{f.board})
			if err != nil {
				t.Fatal(err)
			}
			r, env := firstRequestFixture(t)
			r.SourceRevision, r.RoutingEpoch, r.PlanSHA256, r.ProjectionSHA1, r.B0Cohort = oldSource, plan.Epoch(), plan.SHA256(), plan.ProjectionSHA1(), "c1"
			if format == "legacy-full" {
				// Retained pre-compact SQL documents carry no routing format marker. Use
				// their exact original payload and SHA1; do not rewrite the retained row.
				var payload string
				if err := f.pg.QueryRow(ctx, "SELECT payload FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", plan.SHA256()).Scan(&payload); err != nil {
					t.Fatal(err)
				}
				payload = strings.Replace(payload, `,"routing_projection":"jobseek.ordinary.ownership-projection/v1"}`, `}`, 1)
				sum := sha256.Sum256([]byte(payload))
				legacy := sha1.Sum([]byte(payload))
				r.PlanSHA256, r.ProjectionSHA1 = hex.EncodeToString(sum[:]), hex.EncodeToString(legacy[:])
				if _, err := f.pg.Exec(ctx, "INSERT INTO ordinary_worker_ownership_plan(plan_sha256,routing_epoch,source_revision,payload) VALUES($1,$2,$3,$4)", r.PlanSHA256, r.RoutingEpoch, oldSource, payload); err != nil {
					t.Fatal(err)
				}
			}
			env["LOCAL_DATABASE_URL"], env["REDIS_URL"] = f.dsn, "unix://"+f.r.Options().Addr
			env["ORDINARY_OWNERSHIP_SOURCE_REVISION"], env["ORDINARY_OWNERSHIP_PLAN_SHA256"], env["ORDINARY_OWNERSHIP_PROJECTION_SHA1"] = oldSource, r.PlanSHA256, r.ProjectionSHA1
			env["ORDINARY_OWNERSHIP_ROUTING_EPOCH"], env["LIGHTPANDA_B0_ROUTING_EPOCH"], env["LIGHTPANDA_B0_PRODUCER_COHORT"] = strconv.FormatInt(plan.Epoch(), 10), strconv.FormatInt(plan.Epoch(), 10), "c1"
			write := func() {
				t.Helper()
				body, _ := json.Marshal(r)
				if err := os.WriteFile(env["ORDINARY_FIRST_OWNERSHIP_REQUEST_FILE"], append(body, '\n'), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			write()
			get := func(k string) string { return env[k] }
			c, err := ReadFirstOwnershipAdminConfig(get, oldSource, "activate")
			if err != nil {
				t.Fatal(err)
			}
			if _, err := RunFirstOwnershipAdmin(ctx, c); err != nil {
				t.Fatal("outgoing owner fixture activation", err)
			}
			old, err := queue.OpenOwnedAuthority(ctx, f.dsn, f.client, plan.Epoch(), r.PlanSHA256, oldSource)
			if err != nil {
				t.Fatal(err)
			}
			defer old.Close()
			claim, err := old.Claim(ctx, queue.Simple)
			if err != nil || claim == nil {
				t.Fatal("outgoing interrupted attempt", err)
			}
			var before string
			if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(f)::text FROM ordinary_worker_write_fence f WHERE task_id=$1::uuid", f.board).Scan(&before); err != nil {
				t.Fatal(err)
			}
			r.Operation = "retire"
			env["ORDINARY_GO_WORKER_MODE"] = "retire-first-ownership"
			write()
			call := func() ([]byte, error) {
				command := exec.Command(e.binary, "--retire-first-ownership")
				command.Env = []string{"PATH=" + os.Getenv("PATH")}
				for k, v := range env {
					command.Env = append(command.Env, k+"="+v)
				}
				return command.CombinedOutput()
			}
			if _, err := call(); err == nil {
				t.Fatal("new executable silently adopted outgoing compiled identity")
			}
			adminImage := "ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("1", 64)
			env["ORDINARY_RETIRE_ADMIN_SOURCE_REVISION"], env["ORDINARY_RETIRE_ADMIN_IMAGE_REF"] = installedPlan.SourceRevision(), adminImage
			out, err := call()
			var got queue.FirstOwnershipResult
			if err != nil || json.Unmarshal(out, &got) != nil || got.State != "retired" || got.SourceRevision != oldSource || got.AdminSourceRevision != installedPlan.SourceRevision() || got.AdminImageRef != adminImage {
				t.Fatal("compiled compatibility executable lost distinct identities", err)
			}
			var after string
			if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(f)::text FROM ordinary_worker_write_fence f WHERE task_id=$1::uuid", f.board).Scan(&after); err != nil || before != after {
				t.Fatal("compatibility retirement changed retained attempt", err)
			}
			if f.r.Exists(ctx, "ordinary:ownership:active").Val() != 0 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("compatibility retirement retained native authority")
			}
			if err := old.Heartbeat(ctx, claim); !errors.Is(err, queue.ErrAuthorityLost) {
				t.Fatal("outgoing native writer retained authority", err)
			}
		})
	}
}
