package queue

import (
	"context"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"github.com/jackc/pgx/v5"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func firstOwnershipFixture(t *testing.T) firstOwnerFixture {
	return firstOwnershipFixtureHistory(t, false)
}

func firstOwnershipFixtureHistory(t *testing.T, retainHistory bool) firstOwnerFixture {
	t.Helper()
	ids := []string{}
	if retainHistory {
		ids = append(ids, ordinaryID(t))
	}
	f := greenhouseAuthorityFixture(t, ids...)
	ctx := context.Background()
	// This test explicitly owns an isolated loopback *_ordinary_worker_test
	// database. First adoption must see no previously served native plan; old
	// sequential fixtures deliberately retain such history in that database.
	if !retainHistory {
		if _, err := f.observer.Exec(ctx, "TRUNCATE public.ordinary_worker_ownership_plan CASCADE"); err != nil {
			t.Fatal("private first-owner history reset", err)
		}
	}
	id := ordinaryID(t)
	b0Company := f.company
	if retainHistory {
		if err := f.observer.QueryRow(ctx, "SELECT id::text,company_id::text FROM job_board WHERE board_slug='browser-use-careers'").Scan(&id, &b0Company); err != nil {
			t.Fatal("retained canonical B0 board", err)
		}
	} else {
		_, err := f.observer.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,scrape_interval_hours,
 throttle_key,monitor_needs_browser,scraper_needs_browser)
 VALUES($1::uuid,$2::uuid,'browser-use-careers','https://jobs.example.test/careers','api_sniffer','{}',60,24,'',false,true)`, id, f.company)
		if err != nil {
			t.Fatal("private canonical B0 board", err)
		}
		t.Cleanup(func() { _, _ = f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", id) })
	}
	config := map[string]string{"board_slug": "browser-use-careers", "board_url": "https://jobs.example.test/careers", "crawler_type": "api_sniffer", "company_id": b0Company, "metadata": "{}", "check_interval_minutes": "60", "scrape_interval_hours": "24", "throttle_key": "", "domain": "jobs.example.test", "monitor_needs_browser": "0", "scraper_needs_browser": "1"}
	if err := f.client.redis.HSet(ctx, "board:"+id, config).Err(); err != nil {
		t.Fatal(err)
	}
	lua, err := os.ReadFile("../../src/lua/lightpanda_b0_queue.lua")
	if err != nil {
		t.Fatal(err)
	}
	target, err := CaptureColdB0Target(ctx, f.observer, f.client, f.epoch, "ordinary-first-fixture", "lightpanda-b0", "c1", lua)
	if err != nil {
		t.Fatal("B0 capture", err)
	}
	plan := stageFixturePlan(t, f, strings.Repeat("a", 40))
	seedPublicationB0(t, f.client, target, f.epoch)
	return firstOwnerFixture{f: f, target: target, plan: plan}
}

func applyFirstFixture(t *testing.T, p firstOwnerFixture, retire bool) (*FirstOwnershipResult, error) {
	t.Helper()
	var result *FirstOwnershipResult
	err := WithHostColdSQL(context.Background(), p.f.observer, hostSQLBindingFixture(), func(ctx context.Context, _ *HostColdSQL) error {
		var err error
		result, err = applyFirstOwnership(ctx, p.f.observer, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision(), p.target, retire)
		return err
	})
	return result, err
}

func firstFixtureState(t *testing.T, p firstOwnerFixture) string {
	t.Helper()
	var state string
	if err := p.f.observer.QueryRow(context.Background(), "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", p.plan.digest).Scan(&state); err != nil {
		t.Fatal(err)
	}
	return state
}

func firstFixtureSave(t *testing.T, p firstOwnerFixture, allow bool) {
	t.Helper()
	permission := "-save"
	if allow {
		permission = "+save"
	}
	if err := p.f.client.redis.Do(context.Background(), "ACL", "SETUSER", "default", permission).Err(); err != nil {
		t.Fatal(err)
	}
}

func TestRealFirstOwnershipActivationRetirementPreservesSchedulesAndB0(t *testing.T) {
	p := firstOwnershipFixture(t)
	before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
	if _, err := ActivateFirstOwnershipInHostScope(context.Background(), p.f.observer, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision(), p.target); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("unscoped activation admitted", err)
	}
	result, err := applyFirstFixture(t, p, false)
	if err != nil || result.State != "active" || result.RoutingEpoch != p.f.epoch || result.PlanSHA256 != p.plan.digest {
		t.Fatal("cold first adoption failed", err)
	}
	after := snapshot(t, p.f.client)
	delete(after, ownershipProjectionKey)
	if !reflect.DeepEqual(before, after) || coldCanonicalSnapshot(t, p.f) != canonical {
		t.Fatal("activation changed schedules, canonical data or B0")
	}
	// Completed activation is observation only, including when SAVE is denied.
	firstFixtureSave(t, p, false)
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal("completed activation repeated SAVE", err)
	}
	firstFixtureSave(t, p, true)
	// Restart the actual private server without a final SAVE: only the already
	// acknowledged RDB may supply the active projection and preserved schedules.
	restartPublicationRedisWithoutSave(t, p.f.client)
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal("persisted activation did not survive Redis reload", err)
	}
	native, err := OpenOwnedAuthority(context.Background(), p.f.dsn, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision())
	if err != nil {
		t.Fatal("native owner cannot open", err)
	}
	defer native.Close()
	if claim, err := p.f.client.ClaimLegacyBound(context.Background(), Simple, p.plan); err != nil || claim != nil {
		t.Fatal("legacy claimed native member", err)
	}
	// A legacy scan records cursor progress even when it excludes all members.
	// Retirement must preserve that current queue state as well as deadlines.
	before = snapshot(t, p.f.client)
	delete(before, ownershipProjectionKey)
	if result, err := applyFirstFixture(t, p, true); err != nil || result.State != "retired" {
		t.Fatal("cold retirement failed", err)
	}
	if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || coldCanonicalSnapshot(t, p.f) != canonical {
		t.Fatal("retirement changed schedules, canonical data or B0")
	}
	firstFixtureSave(t, p, false)
	if _, err := applyFirstFixture(t, p, true); err != nil {
		t.Fatal("completed retirement repeated SAVE", err)
	}
	if _, err := applyFirstFixture(t, p, false); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("retired owner readopted", err)
	}
	// Python's unbound queue path can claim the exact preserved schedule again.
	if task, err := p.f.client.Claim(context.Background(), Simple); err != nil || task == nil || task.ID != p.f.task.ID {
		t.Fatal("legacy schedule unavailable after retirement", err)
	}
}

func TestRealFirstOwnershipFreshEpochRetainsRetiredOwnerAndInterruptedReceipt(t *testing.T) {
	p := firstOwnershipFixture(t)
	ctx := context.Background()
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal(err)
	}
	old, err := OpenOwnedAuthority(ctx, p.f.dsn, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	defer old.Close()
	claim, err := old.Claim(ctx, Simple)
	if err != nil || claim == nil {
		t.Fatal("old owner did not claim", err)
	}
	var receiptBefore string
	if err := p.f.observer.QueryRow(ctx, "SELECT to_jsonb(f)::text FROM ordinary_worker_write_fence f WHERE task_id=$1::uuid", p.f.task.ID).Scan(&receiptBefore); err != nil {
		t.Fatal(err)
	}
	if _, err := applyFirstFixture(t, p, true); err != nil {
		t.Fatal("supported old-owner retirement failed", err)
	}
	// A separate isolated Redis/B0 fixture initializes a genuinely fresh
	// serving epoch through production Lua; SQL retains the prior owner and
	// interrupted receipt. This tests admission, not full host B0 reversal.
	next := firstOwnershipFixtureHistory(t, true)
	if next.f.epoch <= p.f.epoch {
		t.Fatal("fixture did not allocate a fresh epoch")
	}
	before, canonical := snapshot(t, next.f.client), coldCanonicalSnapshot(t, p.f)
	if result, err := applyFirstFixture(t, next, false); err != nil || result.State != "active" {
		t.Fatal("fresh owner refused retired history", err)
	}
	after := snapshot(t, next.f.client)
	delete(after, ownershipProjectionKey)
	if !reflect.DeepEqual(before, after) || firstFixtureState(t, p) != "retired" || coldCanonicalSnapshot(t, p.f) != canonical {
		t.Fatal("fresh adoption changed old authority, schedules, B0 or canonical rows")
	}
	var receiptAfter string
	if err := p.f.observer.QueryRow(ctx, "SELECT to_jsonb(f)::text FROM ordinary_worker_write_fence f WHERE task_id=$1::uuid", p.f.task.ID).Scan(&receiptAfter); err != nil || receiptAfter != receiptBefore {
		t.Fatal("fresh adoption rewrote the interrupted historical receipt", err)
	}
	if _, err := old.Write(ctx, claim, true, func(context.Context, pgx.Tx) error {
		t.Fatal("retired old attempt reached its writer")
		return nil
	}); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("retired old attempt retained authority at the fresh epoch", err)
	}
	// Production retained old active attempt records. The next owner must
	// still retire after it has made an interrupted attempt of its own.
	current, currentClaim := firstRetirementClaim(t, next)
	canonical = coldCanonicalSnapshot(t, next.f)
	if _, err := applyFirstFixture(t, next, true); err != nil {
		t.Fatal("current interrupted owner refused legitimate retired history", err)
	}
	if canonical != coldCanonicalSnapshot(t, next.f) {
		t.Fatal("retirement rewrote current or historical attempt records")
	}
	assertFirstRetirementSchedule(t, next, firstRetirementDue(t, next))
	if err := current.Heartbeat(ctx, currentClaim); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("current retired attempt retained authority", err)
	}
}

func TestRealFirstOwnershipFreshEpochRefusesAnotherActiveOwner(t *testing.T) {
	p := firstOwnershipFixture(t)
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal(err)
	}
	next := firstOwnershipFixtureHistory(t, true)
	before, canonical := snapshot(t, next.f.client), coldCanonicalSnapshot(t, next.f)
	if _, err := applyFirstFixture(t, next, false); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("fresh epoch bypassed an unretired active owner", err)
	}
	if !reflect.DeepEqual(before, snapshot(t, next.f.client)) || canonical != coldCanonicalSnapshot(t, next.f) || firstFixtureState(t, next) != "staged" {
		t.Fatal("refusal changed the candidate or serving B0")
	}
}

func TestRealFirstOwnershipSaveFailureAndExactRecovery(t *testing.T) {
	p := firstOwnershipFixture(t)
	firstFixtureSave(t, p, false)
	if _, err := applyFirstFixture(t, p, false); !errors.Is(err, ErrObservation) || firstFixtureState(t, p) != "staged" {
		t.Fatal("unacknowledged publication activated SQL", err)
	}
	firstFixtureSave(t, p, true)
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal("exact activation SAVE recovery", err)
	}
	firstFixtureSave(t, p, false)
	if _, err := applyFirstFixture(t, p, true); !errors.Is(err, ErrObservation) || firstFixtureState(t, p) != "active" {
		t.Fatal("unacknowledged removal retired SQL", err)
	}
	if _, err := applyFirstFixture(t, p, false); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("lost active projection reconstructed", err)
	}
	firstFixtureSave(t, p, true)
	if _, err := applyFirstFixture(t, p, true); err != nil || firstFixtureState(t, p) != "retired" {
		t.Fatal("exact retirement SAVE recovery", err)
	}
}

func TestRealFirstOwnershipCancelsUnservedPublication(t *testing.T) {
	p := firstOwnershipFixture(t)
	firstFixtureSave(t, p, false)
	_, _ = applyFirstFixture(t, p, false)
	firstFixtureSave(t, p, true)
	result, err := applyFirstFixture(t, p, true)
	if err != nil || result.State != "staged" || p.f.client.redis.Exists(context.Background(), ownershipProjectionKey).Val() != 0 {
		t.Fatal("unserved publication not cancelled", err)
	}
}

func TestRealFirstOwnershipNativeClaimCommitAndRetirement(t *testing.T) {
	p := firstOwnershipFixture(t)
	ctx := context.Background()
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal(err)
	}
	native, err := OpenOwnedAuthority(ctx, p.f.dsn, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	defer native.Close()
	claim, err := native.Claim(ctx, Simple)
	if err != nil || claim == nil || claim.Descriptor().ID != p.f.task.ID || !claim.OwnershipBound() {
		t.Fatal("activated owner cannot claim its scheduled member", err)
	}
	if _, err := RetireFirstOwnershipInHostScope(ctx, p.f.observer, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision(), p.target); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("unscoped retirement crossed an active native attempt", err)
	}
	// A failed full-stack startup retains its pending identity. Repeating
	// activation observes the already selected owner without touching a live
	// attempt or repeating SAVE, so the exact native stack can recover it.
	beforeRetry := snapshot(t, p.f.client)
	firstFixtureSave(t, p, false)
	if result, err := applyFirstFixture(t, p, false); err != nil || result.State != "active" {
		t.Fatal("exact active retry refused its retained native attempt", err)
	}
	if !reflect.DeepEqual(beforeRetry, snapshot(t, p.f.client)) {
		t.Fatal("activation retry changed a native lease or schedule")
	}
	firstFixtureSave(t, p, true)
	due := time.Now().UTC().Add(time.Hour)
	receipt, err := native.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
		_, err := tx.Exec(ctx, "UPDATE job_board SET next_check_at=$2 WHERE id=$1::uuid", p.f.task.ID, due)
		return err
	})
	if err != nil {
		t.Fatal("native commit rejected", err)
	}
	if err := native.Settle(ctx, claim, receipt); err != nil {
		t.Fatal("native settlement rejected", err)
	}
	before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
	delete(before, ownershipProjectionKey)
	if _, err := applyFirstFixture(t, p, true); err != nil {
		t.Fatal("completed native owner cannot retire", err)
	}
	if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) {
		t.Fatal("retirement changed committed native receipt or schedule")
	}
	if _, err := native.Claim(ctx, Simple); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("retired native worker retained claim authority", err)
	}
}

func TestFirstOwnershipAuditIsUnmodifiedProductionSource(t *testing.T) {
	body, err := os.ReadFile("../../src/lua/lightpanda_b0_queue.lua")
	if err != nil || !reflect.DeepEqual(body, firstB0AuditLua) {
		t.Fatal("first-adoption audit differs from production B0 source")
	}
}

func TestRealFirstOwnershipRefusesChangedAuthorityBeforeEffects(t *testing.T) {
	for _, change := range []string{"foreign-projection", "expiring-projection", "joint-marker", "b0-lease", "owned-inflight", "b0-cohort", "disabled", "prior-native"} {
		t.Run(change, func(t *testing.T) {
			p := firstOwnershipFixture(t)
			ctx := context.Background()
			var err error
			switch change {
			case "foreign-projection":
				err = p.f.client.redis.Set(ctx, ownershipProjectionKey, "foreign", 0).Err()
			case "expiring-projection":
				err = p.f.client.redis.Set(ctx, ownershipProjectionKey, p.plan.projection, time.Minute).Err()
			case "joint-marker":
				err = p.f.client.redis.Set(ctx, coldPublicationKey, "foreign", 0).Err()
			case "b0-lease":
				err = p.f.client.redis.ZAdd(ctx, p.target.keys()[3], redis.Z{Score: 1, Member: "untracked"}).Err()
			case "owned-inflight":
				err = p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Score: seconds(time.Now().Add(time.Hour)), Member: inflight(p.f.task)}).Err()
			case "b0-cohort":
				err = p.f.client.redis.HSet(ctx, "lightpanda-b0:producer-owner", "cohort", "c2").Err()
			case "disabled":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", p.f.task.ID)
			case "prior-native":
				previous := stageFixturePlan(t, p.f, strings.Repeat("b", 40))
				activateFixturePlan(t, p.f, previous)
				_, err = p.f.observer.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1", previous.digest)
			}
			if err != nil {
				t.Fatal("authority change fixture", err)
			}
			before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
			if _, err := applyFirstFixture(t, p, false); !errors.Is(err, ErrAuthorityLost) && !errors.Is(err, ErrUnsupportedProfile) {
				t.Fatal("changed authority admitted", err)
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) || firstFixtureState(t, p) != "staged" {
				t.Fatal("refusal changed data or ownership")
			}
		})
	}
}

type firstOwnerFixture struct {
	f      authorityFixture
	target *ColdB0Target
	plan   *OwnershipPlan
}

func seedPublicationB0(t *testing.T, c *Client, target *ColdB0Target, epoch int64) {
	t.Helper()
	ctx := context.Background()
	args := target.auditArguments(epoch)
	args[0] = "initialize_producer"
	reply, err := c.redis.Eval(ctx, target.lua, target.keys(), args...).Slice()
	if err != nil || len(reply) != 12 || reply[0] != "accepted" {
		t.Fatal("real B0 producer initialization rejected")
	}
	// Start from the frozen actual Python task codec, then change ONLY this
	// private board/task/route identity. The actual reviewed Lua creates/indexes
	// the ready record and legacy exclusion guard; no fabricated queue records.
	data, err := os.ReadFile("../../contracts/v1/b0task/testdata/python_tasks.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Cases []struct {
			Payload string `json:"payload"`
		} `json:"cases"`
	}
	if json.Unmarshal(data, &corpus) != nil || len(corpus.Cases) == 0 {
		t.Fatal("actual task capture unavailable")
	}
	var envelope map[string]any
	if json.Unmarshal([]byte(corpus.Cases[0].Payload), &envelope) != nil {
		t.Fatal("task capture malformed")
	}
	taskID := ordinaryID(t)
	envelope["task_id"] = taskID
	envelope["board_id"] = target.document.Boards[0].ID
	envelope["routing_epoch"] = epoch
	envelope["shard_id"] = target.document.ShardID
	payload, _ := json.Marshal(envelope)
	sha := sha256.Sum256(payload)
	legacy := sha1.Sum(payload)
	config := map[string]string{"domain": "jobs.example.test", "board_id": target.document.Boards[0].ID, "source_url": "https://jobs.example.test/posting", "description_r2_hash": "", "scrape_step": "0", "scrape_interval_hours": "24"}
	configBody, _ := json.Marshal(config)
	args = target.auditArguments(epoch)
	args[0] = "activate_legacy"
	args[4] = taskID
	args[5] = "3"
	args[10] = string(payload)
	args[11] = hex.EncodeToString(sha[:])
	args[12] = hex.EncodeToString(legacy[:])
	args[18] = string(configBody)
	args[22] = "1"
	reply, err = c.redis.Eval(ctx, target.lua, target.keys(), args...).Slice()
	if err != nil || len(reply) != 12 || reply[0] != "accepted" {
		t.Fatalf("real B0 task transfer rejected: %v %v", reply, err)
	}
}

func restartPublicationRedisWithoutSave(t *testing.T, c *Client) {
	t.Helper()
	socket := c.redis.Options().Addr
	root := filepath.Dir(socket)
	if !strings.HasPrefix(root, "/tmp/jq-") {
		t.Fatal("restart requires owned private Redis directory")
	}
	_ = c.redis.ShutdownNoSave(context.Background()).Err()
	deadline := time.Now().Add(3 * time.Second)
	for {
		_, err := os.Lstat(socket)
		if errors.Is(err, os.ErrNotExist) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("private Redis did not stop")
		}
		time.Sleep(10 * time.Millisecond)
	}
	log, err := os.CreateTemp(root, "restart-*.log")
	if err != nil {
		t.Fatal(err)
	}
	command := exec.Command("redis-server", "--port", "0", "--unixsocket", socket, "--unixsocketperm", "700", "--save", "", "--appendonly", "no", "--dir", root)
	command.Env = []string{"PATH=" + os.Getenv("PATH"), "LC_ALL=C", "LANG=C"}
	command.Stdout, command.Stderr = log, log
	if command.Start() != nil {
		_ = log.Close()
		t.Fatal("private Redis restart unavailable")
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	t.Cleanup(func() {
		_ = command.Process.Signal(os.Interrupt)
		select {
		case <-done:
		case <-time.After(3 * time.Second):
			_ = command.Process.Kill()
			<-done
		}
		_ = log.Close()
	})
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	for c.redis.Ping(ctx).Err() != nil {
		if ctx.Err() != nil {
			t.Fatal("private RDB restart failed")
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func coldCanonicalSnapshot(t *testing.T, f authorityFixture) string {
	t.Helper()
	var body string
	err := f.observer.QueryRow(context.Background(), `SELECT jsonb_build_object(
 'board',(SELECT to_jsonb(b) FROM job_board b WHERE id=$1::uuid),
 'posting',(SELECT to_jsonb(p) FROM job_posting p WHERE id=$1::uuid),
 'descriptions',(SELECT jsonb_agg(to_jsonb(d) ORDER BY locale) FROM descriptions d WHERE posting_id=$1::uuid),
 'receipts',(SELECT jsonb_agg(to_jsonb(f) ORDER BY task_kind,task_id) FROM ordinary_worker_write_fence f WHERE board_id=$1::uuid))::text`, f.task.ID).Scan(&body)
	if err != nil {
		t.Fatal("canonical transition snapshot unavailable")
	}
	return body
}
