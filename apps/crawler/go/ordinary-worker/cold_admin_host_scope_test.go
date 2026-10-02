package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func TestRealColdAdminBorrowsLiveHostSQLSessionForIntentReservationAndExactRetry(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	source := ordinaryFixtureSourceRevision(t)
	// The private local fixture owns all rows. Retire its setup owner before
	// staging a fresh first-transition plan; this is not production authority.
	if _, err := f.pg.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE state='active'"); err != nil {
		t.Fatal("private owner retirement")
	}
	if f.r.Del(ctx, "ordinary:ownership:active").Err() != nil {
		t.Fatal("private setup projection retirement")
	}
	var previous int64
	if f.pg.QueryRow(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')").Scan(&previous) != nil {
		t.Fatal("private setup epoch")
	}
	stage, err := queue.OpenAuthority(ctx, f.dsn, f.client, previous)
	if err != nil {
		t.Fatal("private staging authority")
	}
	prepared, err := stage.StageGreenhouseOwnership(ctx, source, []string{f.board})
	stage.Close()
	if err != nil {
		t.Fatal("private prepared plan")
	}
	b0 := fixtureID(t)
	if _, err := f.pg.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser)
 VALUES($1::uuid,$2::uuid,'browser-use-careers','https://jobs.example.test/careers','api_sniffer','{}',60,24,'',false,true)`, b0, f.company); err != nil {
		t.Fatal("private B0 board")
	}
	if f.r.HSet(ctx, "board:"+b0, map[string]string{"board_slug": "browser-use-careers", "board_url": "https://jobs.example.test/careers", "crawler_type": "api_sniffer", "company_id": f.company, "metadata": `{}`, "check_interval_minutes": "60", "scrape_interval_hours": "24", "throttle_key": "", "domain": "jobs.example.test", "monitor_needs_browser": "0", "scraper_needs_browser": "1"}).Err() != nil {
		t.Fatal("private B0 projection")
	}
	t.Cleanup(func() {
		// Only the validated private *_ordinary_worker_test database may discard
		// its own retained test journals; production retains these permanently.
		_, err := f.pg.Exec(context.Background(), "TRUNCATE crawler_ownership_restoration_completion,crawler_ownership_restoration_publication,crawler_ownership_restoration_finalization,crawler_ownership_b0_reactivation_completion,crawler_ownership_b0_reactivation,crawler_ownership_ordinary_restoration,crawler_ownership_b0_forward_completion,crawler_ownership_b0_forward,crawler_ownership_b0_restoration,crawler_ownership_reversal,crawler_ownership_transition,crawler_ownership_b0_target")
		if err != nil {
			t.Error("private journal cleanup")
		}
	})
	state := hostPrivateDirectory(t)
	lua, err := os.ReadFile("../../src/lua/lightpanda_b0_queue.lua")
	if err != nil || os.WriteFile(filepath.Join(state, "b0.lua"), lua, 0600) != nil {
		t.Fatal("private retained Lua")
	}
	canonical, redisBefore := coldExecutableCanonicalSnapshot(t, f), fullColdExecutableRedisSnapshot(t, f)
	var retainedScope context.Context
	var reserved *ColdAdminIdentity
	err = queue.WithHostColdSQL(ctx, f.pg, queue.HostColdSQLBinding{SourceRevision: source, RequestSHA256: strings.Repeat("b", 64), ContainmentIntentSHA256: strings.Repeat("c", 64)}, func(scoped context.Context, sql *queue.HostColdSQL) error {
		retainedScope = scoped
		target, err := queue.CaptureColdB0Target(scoped, f.pg, f.client, previous, "ordinary-host-scope", "lightpanda-b0", "c1", lua)
		if err != nil {
			return err
		}
		spec := queue.ColdTransitionSpec{Version: "jobseek.crawler.cold-transition/v1", TransitionID: fixtureID(t), SourceRevision: source, PreviousEpoch: previous, PreparedPlanSHA256: prepared.SHA256(), TargetB0ManifestSHA256: target.SHA256(), ActiveReleaseSHA256: strings.Repeat("3", 64), TargetReleaseSHA256: strings.Repeat("4", 64), RollbackReleaseSHA256: strings.Repeat("3", 64), ColdAttestationSHA256: strings.Repeat("5", 64)}
		body, _ := json.Marshal(spec)
		intent := hostDigest(body)
		if os.WriteFile(filepath.Join(state, "intent.json"), body, 0600) != nil || os.WriteFile(filepath.Join(state, "target.json"), []byte(target.Payload()), 0600) != nil {
			t.Fatal("private immutable input fixture")
		}
		env := map[string]string{"ORDINARY_OWNERSHIP_SOURCE_REVISION": source, "ORDINARY_COLD_ROUTING_EPOCH": fmt.Sprint(previous), "ORDINARY_COLD_INTENT_FILE": filepath.Join(state, "intent.json"), "ORDINARY_COLD_INTENT_SHA256": intent, "LOCAL_DATABASE_URL": "invalid-caller-url", "REDIS_URL": "invalid-caller-url"}
		config := func(operation string) ColdAdminConfig {
			t.Helper()
			env["ORDINARY_GO_WORKER_MODE"] = operation
			for _, key := range []string{"ORDINARY_COLD_B0_TARGET_FILE", "ORDINARY_COLD_B0_TARGET_SHA256", "ORDINARY_COLD_B0_LUA_FILE"} {
				delete(env, key)
			}
			if operation == "cold-begin" {
				env["ORDINARY_COLD_B0_TARGET_FILE"], env["ORDINARY_COLD_B0_TARGET_SHA256"], env["ORDINARY_COLD_B0_LUA_FILE"] = filepath.Join(state, "target.json"), target.SHA256(), filepath.Join(state, "b0.lua")
			}
			c, err := ReadColdAdminConfig(func(key string) string { return env[key] }, source, operation)
			if err != nil {
				t.Fatal("explicit borrowed operation config", operation)
			}
			return c
		}
		assertExcluded := func() {
			t.Helper()
			var observation struct {
				Keys []int64 `json:"exclusive_barriers"`
			}
			if json.Unmarshal([]byte(sql.Body()), &observation) != nil || len(observation.Keys) != 3 || sql.Check(scoped) != nil {
				t.Fatal("borrowed operation lost live scope")
			}
			if err := pgx.BeginFunc(scoped, f.pg, func(tx pgx.Tx) error {
				for _, key := range observation.Keys {
					var entered bool
					if err := tx.QueryRow(scoped, "SELECT pg_try_advisory_xact_lock_shared($1)", key).Scan(&entered); err != nil || entered {
						t.Fatal("writer entered borrowed cold phase", key)
					}
				}
				return nil
			}); err != nil {
				t.Fatal("actual writer probe", err)
			}
		}
		other, err := pgxpool.New(scoped, f.dsn)
		if err != nil {
			t.Fatal("same-endpoint different pool fixture")
		}
		defer other.Close()
		valid := config("cold-begin")
		if result, err := RunColdAdminInHostScope(scoped, valid, other, f.client); err != ErrStartup || result != nil {
			t.Fatal("different pool adopted live scope")
		}
		wrongSource := valid
		wrongSource.source = strings.Repeat("f", 40)
		if result, err := RunColdAdminInHostScope(scoped, wrongSource, f.pg, f.client); err != ErrStartup || result != nil {
			t.Fatal("different source adopted live scope")
		}
		begun, err := RunColdAdminInHostScope(scoped, config("cold-begin"), f.pg, f.client)
		if err != nil || begun.IntentSHA256 != intent {
			t.Fatal("borrowed intent failed", err)
		}
		assertExcluded()
		var phase string
		if f.pg.QueryRow(scoped, "SELECT phase FROM crawler_ownership_transition WHERE intent_sha256=$1", intent).Scan(&phase) != nil || phase != "pending" {
			t.Fatal("intent not committed before reservation")
		}
		reserved, err = RunColdAdminInHostScope(scoped, config("cold-reserve"), f.pg, f.client)
		if err != nil || reserved.RoutingEpoch <= previous || !planPattern.MatchString(reserved.PlanSHA256) {
			t.Fatal("borrowed reserve failed", err)
		}
		assertExcluded()
		retry, err := RunColdAdminInHostScope(scoped, config("cold-reserve"), f.pg, f.client)
		if err != nil || !reflect.DeepEqual(reserved, retry) {
			t.Fatal("borrowed retry allocated a new identity", err)
		}
		inspection, err := RunColdAdminInHostScope(scoped, config("cold-inspect"), f.pg, f.client)
		if err != nil || inspection.RetainedPhase != "reserved" || inspection.PlanSHA256 != reserved.PlanSHA256 || inspection.RoutingEpoch != reserved.RoutingEpoch {
			t.Fatal("borrowed inspection lost committed phase", err)
		}
		assertExcluded()
		return nil
	})
	if err != nil {
		t.Fatal("actual host cold command scope", err)
	}
	if queue.CheckHostColdSQLScope(retainedScope, f.pg, source) == nil {
		t.Fatal("returned context admitted a later cold phase")
	}
	if f.pg.Ping(ctx) != nil {
		t.Fatal("borrowed pool closed")
	}
	if _, err := queue.CaptureColdB0Target(ctx, f.pg, f.client, reserved.RoutingEpoch, "ordinary-host-scope", "lightpanda-b0", "c1", lua); err != nil {
		t.Fatal("borrowed Redis client closed", err)
	}
	if coldExecutableCanonicalSnapshot(t, f) != canonical || !reflect.DeepEqual(redisBefore, fullColdExecutableRedisSnapshot(t, f)) {
		t.Fatal("intent/reservation changed canonical or Redis state")
	}
	var epoch int64
	if f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epoch) != nil || epoch != reserved.RoutingEpoch {
		t.Fatal("exact retry consumed another epoch")
	}
	t.Log("actual in-process cold target/intent/reservation/inspection reused the live host SQL session; all three shared writer barriers refused across independent commits; intent durable before reservation, exact retry no new epoch, canonical and complete Redis values conserved, borrowed resources remain open; fixture release hashes only and runtime admission unproven")
}

func TestColdAdminBorrowingRejectsMissingLiveScopeBeforeInputsOrEndpoints(t *testing.T) {
	c := ColdAdminConfig{source: strings.Repeat("a", 40), operation: "cold-begin", database: "invalid", redis: "invalid"}
	if result, err := RunColdAdminInHostScope(context.Background(), c, nil, nil); err != ErrStartup || result != nil {
		t.Fatal("unscoped borrowed command admitted")
	}
}
