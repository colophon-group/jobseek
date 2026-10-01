package queue

import (
	"context"
	"errors"
	"fmt"
	"os"
	"reflect"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func TestSharedHostScriptsMatchActualPython(t *testing.T) {
	body, err := os.ReadFile("../../src/redis_queue.py")
	if err != nil {
		t.Fatal(err)
	}
	for name, expected := range map[string]string{"_RECORD_HOST_FAILURE_LUA": hostFailureLua, "_RECORD_HOST_SUCCESS_LUA": hostSuccessLua} {
		match := regexp.MustCompile(`(?s)` + name + ` = """(.*?)"""`).FindSubmatch(body)
		if len(match) != 2 || string(match[1]) != expected {
			t.Fatal("shared production host script differs: " + name)
		}
	}
}

func fixtureCircuits(t *testing.T, client *Client) *HostCircuits {
	t.Helper()
	h, err := NewHostCircuits(client, DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func TestRealSharedHostFailureAndRecovery(t *testing.T) {
	c := privateRedis(t)
	h := fixtureCircuits(t, c)
	ctx := context.Background()
	host := "apply.example.com"
	for count := 1; count <= 3; count++ {
		r := h.recordFailure(ctx, host)
		if r.Diagnostic != nil || r.Failures != int64(count) || r.OpenedNow != (count == 3) || (r.OpenUntil != nil) != (count == 3) {
			t.Fatalf("threshold transition %d: %+v", count, r)
		}
	}
	open := c.redis.Get(ctx, "host_open:"+host).Val()
	if r := h.recordFailure(ctx, host); r.Diagnostic != nil || r.OpenedNow || c.redis.Get(ctx, "host_open:"+host).Val() != open {
		t.Fatal("late failure extended open interval")
	}
	if ttl := c.redis.TTL(ctx, "host_fail:"+host).Val(); ttl <= 590*time.Second || ttl > 600*time.Second {
		t.Fatal("failure window differs")
	}
	if err := c.redis.Set(ctx, "host_probe:"+host, "1", 600*time.Second).Err(); err != nil {
		t.Fatal(err)
	}
	if err := h.recordSuccess(ctx, host); err != nil || c.redis.Exists(ctx, "host_fail:"+host).Val() != 0 || c.redis.Get(ctx, "host_open:"+host).Val() != open || c.redis.Exists(ctx, "host_probe:"+host).Val() != 1 {
		t.Fatal("inflight success closed an unexpired circuit")
	}
	now, _ := c.clock(ctx)
	if err := c.redis.Set(ctx, "host_open:"+host, number(now-1), time.Hour).Err(); err != nil {
		t.Fatal(err)
	}
	if r := h.recordFailure(ctx, host); r.Diagnostic != nil || !r.OpenedNow || r.Failures != 1 || c.redis.Exists(ctx, "host_probe:"+host).Val() != 0 {
		t.Fatal("failed half-open probe did not reopen immediately")
	}
	if err := c.redis.Set(ctx, "host_open:"+host, number(now-1), time.Hour).Err(); err != nil {
		t.Fatal(err)
	}
	var winners atomic.Int64
	var group sync.WaitGroup
	for range 64 {
		group.Add(1)
		go func() {
			defer group.Done()
			if c.redis.SetNX(ctx, "host_probe:"+host, "1", 600*time.Second).Val() {
				winners.Add(1)
			}
		}()
	}
	group.Wait()
	if winners.Load() != 1 {
		t.Fatal("half-open circuit admitted multiple probes")
	}
	if err := h.recordSuccess(ctx, host); err != nil || c.redis.Exists(ctx, "host_open:"+host, "host_fail:"+host, "host_probe:"+host).Val() != 0 {
		t.Fatal("recovery probe retained circuit state")
	}
	for _, bad := range []string{"corrupt", "NaN", "+Inf", "-1"} {
		if err := c.redis.Set(ctx, "host_open:"+host, bad, time.Hour).Err(); err != nil {
			t.Fatal(err)
		}
		if open, err := h.openUntil(ctx, host); err != nil || open != nil || c.redis.Exists(ctx, "host_open:"+host).Val() != 0 {
			t.Fatal("bad marker prevented fail-open")
		}
	}
}

func TestRealOwnedHostPreflightCanonicalDeferral(t *testing.T) {
	for _, mode := range []string{"open", "half_open", "probe", "board_fallback", "unavailable"} {
		t.Run(mode, func(t *testing.T) {
			f, a := lifecycleFixture(t, `{}`)
			ctx := context.Background()
			h := fixtureCircuits(t, f.client)
			host := "apply.example.com"
			if mode != "board_fallback" {
				if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "egress_host", " Apply.Example.COM. ").Err(); err != nil {
					t.Fatal(err)
				}
			}
			claim, err := a.Claim(ctx, Simple)
			if err != nil || claim == nil {
				t.Fatal("missing claim", err)
			}
			now, _ := f.client.clock(ctx)
			open := now + 1800
			if mode == "half_open" || mode == "probe" {
				open = now - 1
			}
			if mode == "board_fallback" {
				host = "boards-api.greenhouse.io"
			}
			if mode == "unavailable" {
				if err := f.client.redis.LPush(ctx, "host_open:"+host, "wrong-type").Err(); err != nil {
					t.Fatal(err)
				}
			} else {
				if err := f.client.redis.Set(ctx, "host_open:"+host, number(open), time.Hour).Err(); err != nil {
					t.Fatal(err)
				}
			}
			if mode == "half_open" {
				if err := f.client.redis.Set(ctx, "host_probe:"+host, "1", time.Hour).Err(); err != nil {
					t.Fatal(err)
				}
			}
			before := claim.Descriptor().Config
			result, err := a.PreflightGreenhouseHost(ctx, claim, h)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(before, claim.Descriptor().Config) {
				t.Fatal("preflight changed inflight snapshot")
			}
			if mode == "open" || mode == "half_open" {
				if result.Receipt == nil || !result.Receipt.NextDue().After(time.Now()) || result.Status != "host_circuit_"+mode {
					t.Fatalf("missing canonical deferral: %+v", result)
				}
				var failures int
				var success *time.Time
				if err := f.observer.QueryRow(ctx, "SELECT consecutive_failures,last_success_at FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&failures, &success); err != nil || failures != 0 {
					t.Fatal("deferral spent failure budget", err)
				}
				if err := a.Settle(ctx, claim, result.Receipt); err != nil {
					t.Fatal(err)
				}
				score := f.client.redis.ZScore(ctx, "monitors_simple:greenhouse", f.task.ID).Val()
				if score != seconds(*result.Receipt.NextDue()) {
					t.Fatal("queue ignored canonical deferral")
				}
			} else {
				want := map[string]string{"probe": "probe", "board_fallback": "ready", "unavailable": "unavailable"}[mode]
				if result.Receipt != nil || result.Status != want || mode == "unavailable" && result.Diagnostic == nil {
					t.Fatalf("unexpected preflight: %+v", result)
				}
				if mode == "board_fallback" && result.Host != "job-boards.greenhouse.io" {
					t.Fatal("preflight silently chose API hostname", result.Host)
				}
				if _, err := a.BeginGreenhouseCycle(ctx, claim); err != nil {
					t.Fatal("ready/probe/fail-open run denied discovery", err)
				}
				if _, err := a.PreflightGreenhouseHost(ctx, claim, h); !errors.Is(err, ErrConfiguration) {
					t.Fatal("preflight was allowed after cycle start")
				}
			}
		})
	}
}

func hostCycle(t *testing.T) (authorityFixture, *Authority, *GreenhouseCycle, *GreenhouseHostRun) {
	t.Helper()
	f, a := lifecycleFixture(t, `{}`)
	ctx := context.Background()
	claim, err := a.Claim(ctx, Simple)
	if err != nil || claim == nil {
		t.Fatal("missing host-cycle claim", err)
	}
	preflight, err := a.PreflightGreenhouseHost(ctx, claim, fixtureCircuits(t, f.client))
	if err != nil || preflight.Receipt != nil {
		t.Fatal("host-cycle preflight failed", err)
	}
	cycle, err := a.BeginGreenhouseCycle(ctx, claim)
	if err != nil {
		t.Fatal(err)
	}
	return f, a, cycle, preflight.Run
}

func TestRealOwnedHostFailureReceiptAndAtomicPublication(t *testing.T) {
	f, a, c, run := hostCycle(t)
	ctx := context.Background()
	host := "boards-api.greenhouse.io"
	if err := f.client.redis.Set(ctx, "host_fail:"+host, "2", 600*time.Second).Err(); err != nil {
		t.Fatal(err)
	}
	before := c.claim.Descriptor().Config
	result, err := c.FinishFailureWithHostCircuit(ctx, "http_status", run, GreenhouseHostObservation{LastHost: host})
	if err != nil || result.HostCircuit.Diagnostic != nil || !result.HostCircuit.OpenedNow || result.Receipt.learnedHost == nil || *result.Receipt.learnedHost != host {
		t.Fatal("failure did not bind host/deadline", err)
	}
	if result.Receipt.NextDue().Before(*result.HostCircuit.OpenUntil) {
		t.Fatal("canonical backoff lost circuit lower bound")
	}
	if !reflect.DeepEqual(before, c.claim.Descriptor().Config) || f.client.redis.HGet(ctx, "board:"+f.task.ID, "egress_host").Val() != "" {
		t.Fatal("learned host changed inflight configuration")
	}
	// Corrupt retained host is rejected before any queue or cache effect.
	if _, err := f.observer.Exec(ctx, "UPDATE ordinary_worker_write_fence SET learned_egress_host='other.example.com' WHERE task_id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	if err := a.Settle(ctx, c.claim, result.Receipt); !errors.Is(err, ErrAuthorityLost) || f.client.redis.HGet(ctx, "board:"+f.task.ID, "egress_host").Val() != "" {
		t.Fatal("mismatched receipt published host")
	}
	if _, err := f.observer.Exec(ctx, "UPDATE ordinary_worker_write_fence SET learned_egress_host=$2 WHERE task_id=$1::uuid", f.task.ID, host); err != nil {
		t.Fatal(err)
	}
	settleLifecycle(t, f, c, result)
	if f.client.redis.HGet(ctx, "board:"+f.task.ID, "egress_host").Val() != host {
		t.Fatal("settlement lost learned host")
	}
	dueLifecycle(t, f)
	claim, err := a.Claim(ctx, Simple)
	if err != nil || claim == nil || claim.Descriptor().Config["egress_host"] != host {
		t.Fatal("future claim did not observe published host", err)
	}
	preflight, err := a.PreflightGreenhouseHost(ctx, claim, fixtureCircuits(t, f.client))
	if err != nil || preflight.Status != "host_circuit_open" || preflight.Host != host {
		t.Fatal("future claim did not use shared learned circuit", err)
	}
	if err := a.Settle(ctx, claim, preflight.Receipt); err != nil {
		t.Fatal(err)
	}
}

func TestRealOwnedHostReceiptRecoveryAfterProcessLoss(t *testing.T) {
	f, a, c, run := hostCycle(t)
	ctx := context.Background()
	host := "redirected.example.com"
	result, err := c.FinishFailureWithHostCircuit(ctx, "body_failed", run, GreenhouseHostObservation{LastHost: host})
	if err != nil {
		t.Fatal(err)
	}
	plan := a.ownership
	a.Close()
	expire(t, f.client, &c.claim.task)
	if _, err := guardedReap(t, f); err != nil {
		t.Fatal(err)
	}
	replacement, err := OpenOwnedAuthority(ctx, f.dsn, f.client, f.epoch, plan.digest, plan.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	defer replacement.Close()
	claim, err := replacement.Claim(ctx, Simple)
	if err != nil || claim == nil || claim.RecoveredReceipt() == nil {
		t.Fatal("host receipt did not recover", err)
	}
	receipt := claim.RecoveredReceipt()
	if receipt.learnedHost == nil || *receipt.learnedHost != host || !receipt.NextDue().Equal(*result.Receipt.NextDue()) {
		t.Fatal("recovered receipt lost host/deadline")
	}
	if err := replacement.Settle(ctx, claim, receipt); err != nil {
		t.Fatal(err)
	}
	if f.client.redis.HGet(ctx, "board:"+f.task.ID, "egress_host").Val() != host || f.client.redis.Get(ctx, "host_fail:"+host).Val() != "1" {
		t.Fatal("recovery lost host or repeated failed run")
	}
}

func TestRealOwnedHostFailureRollbackDoesNotReplayCircuit(t *testing.T) {
	f, _, c, run := hostCycle(t)
	ctx := context.Background()
	host := "failure.example.com"
	name := "ordinary_host_" + strings.ReplaceAll(ordinaryID(t), "-", "")
	function := pgx.Identifier{name}.Sanitize()
	trigger := pgx.Identifier{name + "_trigger"}.Sanitize()
	sql := fmt.Sprintf(`CREATE FUNCTION %s() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.task_id::text='%s' AND NEW.state='completed' THEN RAISE EXCEPTION 'private host rollback'; END IF; RETURN NEW; END $$; CREATE TRIGGER %s BEFORE UPDATE ON ordinary_worker_write_fence FOR EACH ROW EXECUTE FUNCTION %s()`, function, f.task.ID, trigger, function)
	if _, err := f.observer.Exec(ctx, sql); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.observer.Exec(ctx, "DROP TRIGGER IF EXISTS "+trigger+" ON ordinary_worker_write_fence")
		_, _ = f.observer.Exec(ctx, "DROP FUNCTION IF EXISTS "+function+"()")
	})
	if result, err := c.FinishFailureWithHostCircuit(ctx, "http_status", run, GreenhouseHostObservation{LastHost: host}); err == nil || result != nil {
		t.Fatal("rollback issued receipt")
	}
	var count int
	if err := f.observer.QueryRow(ctx, "SELECT consecutive_failures FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&count); err != nil || count != 0 {
		t.Fatal("rollback leaked board failure")
	}
	if f.client.redis.Get(ctx, "host_fail:"+host).Val() != "1" || f.client.redis.HGet(ctx, "board:"+f.task.ID, "egress_host").Val() != "" {
		t.Fatal("protective outcome or snapshot differs")
	}
	if _, err := c.FinishSuccess(ctx, GreenhouseInventorySummary{}); !errors.Is(err, ErrConfiguration) {
		t.Fatal("failed run recovered success authority")
	}
	if _, err := f.observer.Exec(ctx, "DROP TRIGGER "+trigger+" ON ordinary_worker_write_fence"); err != nil {
		t.Fatal(err)
	}
	result, err := c.FinishFailureWithHostCircuit(ctx, "http_status", run, GreenhouseHostObservation{LastHost: host})
	if err != nil {
		t.Fatal(err)
	}
	if f.client.redis.Get(ctx, "host_fail:"+host).Val() != "1" {
		t.Fatal("SQL retry counted failed run twice")
	}
	settleLifecycle(t, f, c, result)
}

func TestRealOwnedHostSuccessAndStaleOutcomeGuards(t *testing.T) {
	f, a, c, run := hostCycle(t)
	ctx := context.Background()
	host := "boards-api.greenhouse.io"
	now, _ := f.client.clock(ctx)
	for key, value := range map[string]string{"host_fail:": "3", "host_open:": number(now - 1), "host_probe:": "1"} {
		if err := f.client.redis.Set(ctx, key+host, value, time.Hour).Err(); err != nil {
			t.Fatal(err)
		}
	}
	result, err := c.FinishSuccess(ctx, GreenhouseInventorySummary{})
	if err != nil {
		t.Fatal(err)
	}
	if err := a.RecordGreenhouseHostSuccess(ctx, run, result.Receipt, GreenhouseHostObservation{Hosts: []string{host, host}}); err != nil {
		t.Fatal(err)
	}
	if f.client.redis.Exists(ctx, "host_fail:"+host, "host_open:"+host, "host_probe:"+host).Val() != 0 {
		t.Fatal("success did not recover observed host")
	}
	if err := f.client.redis.Set(ctx, "host_fail:"+host, "2", time.Hour).Err(); err != nil {
		t.Fatal(err)
	}
	if err := a.RecordGreenhouseHostSuccess(ctx, run, result.Receipt, GreenhouseHostObservation{Hosts: []string{host}}); err != nil || f.client.redis.Get(ctx, "host_fail:"+host).Val() != "2" {
		t.Fatal("duplicate success replayed circuit transition")
	}
	settleLifecycle(t, f, c, result)
}

func TestRealOwnedHostStaleOutcomeGuard(t *testing.T) {
	ctx := context.Background()
	// A late run must be rejected before advancing its host.
	f2, _, c2, run2 := hostCycle(t)
	expire(t, f2.client, &c2.claim.task)
	if result, err := c2.FinishFailureWithHostCircuit(ctx, "timeout", run2, GreenhouseHostObservation{LastHost: "stale.example.com"}); !errors.Is(err, ErrAuthorityLost) || result != nil || f2.client.redis.Exists(ctx, "host_fail:stale.example.com").Val() != 0 {
		t.Fatal("stale attempt mutated shared circuit")
	}
}

func TestRealOwnedHostCircuitTroubleDoesNotHideCanonicalFailure(t *testing.T) {
	f, a, c, run := hostCycle(t)
	ctx := context.Background()
	host := "unavailable.example.com"
	// The actual Lua increments before this GET fails: its reply is ambiguous.
	if err := f.client.redis.LPush(ctx, "host_open:"+host, "wrong-type").Err(); err != nil {
		t.Fatal(err)
	}
	result, err := c.FinishFailureWithHostCircuit(ctx, "http_status", run, GreenhouseHostObservation{LastHost: host})
	if err != nil || result == nil || result.HostCircuit.Diagnostic == nil || result.Receipt == nil {
		t.Fatal("protective circuit trouble hid canonical outcome", err)
	}
	if result.Receipt.NextDue().Before(time.Now().Add(4*time.Minute)) || f.client.redis.Get(ctx, "host_fail:"+host).Val() != "1" {
		t.Fatal("canonical backoff or failed-run accounting differs")
	}
	if err := a.RecordGreenhouseHostSuccess(ctx, run, result.Receipt, GreenhouseHostObservation{Hosts: []string{host}}); !errors.Is(err, ErrConfiguration) {
		t.Fatal("failure receipt authorized host success")
	}
	settleLifecycle(t, f, c, result)
}

func TestRealOwnedHostOldEpochCannotPublishReceipt(t *testing.T) {
	f, a, c, run := hostCycle(t)
	ctx := context.Background()
	host := "retired.example.com"
	result, err := c.FinishFailureWithHostCircuit(ctx, "timeout", run, GreenhouseHostObservation{LastHost: host})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.observer.Exec(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')"); err != nil {
		t.Fatal(err)
	}
	if err := a.Settle(ctx, c.claim, result.Receipt); !errors.Is(err, ErrAuthorityLost) || f.client.redis.HGet(ctx, "board:"+f.task.ID, "egress_host").Val() != "" {
		t.Fatal("retired epoch published learned host")
	}
}

func TestHostCircuitSettingsRejectInvalidStartup(t *testing.T) {
	if _, err := NewHostCircuits(nil, DefaultHostCircuitSettings()); !errors.Is(err, ErrConfiguration) {
		t.Fatal("nil shared queue accepted")
	}
	c := privateRedis(t)
	for _, settings := range []HostCircuitSettings{{}, {0, time.Second, time.Second, time.Second}, {3, 0, time.Second, time.Second}, {3, time.Second, 0, time.Second}, {3, time.Second, time.Second, 0}, {3, time.Millisecond, time.Second, time.Second}} {
		if _, err := NewHostCircuits(c, settings); !errors.Is(err, ErrConfiguration) {
			t.Fatal("invalid startup bounds accepted")
		}
	}
}
