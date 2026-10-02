package queue

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
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestColdPublicationSchemaMatchesAppliedMigration(t *testing.T) {
	body, err := os.ReadFile("../../src/migrations/sql/crawler_ownership_publication.sql")
	if err != nil || string(body) != coldPublicationSchema {
		t.Fatal("publication schema differs from migration")
	}
	lua, err := os.ReadFile("../../src/lua/lightpanda_b0_queue.lua")
	hash := sha256.Sum256(lua)
	if err != nil || hex.EncodeToString(hash[:]) != coldB0LuaSHA256 {
		t.Fatal("B0 witness differs from production lifecycle source")
	}
}

func TestColdB0MetadataRejectsDuplicateNestedKeysAndUnboundedDepth(t *testing.T) {
	for _, raw := range []string{`{"x":1,"x":2}`, `{"config":{"x":1,"x":2}}`, `null`, `[]`, `{} true`, strings.Repeat(`{"x":`, 65) + "0" + strings.Repeat("}", 65)} {
		if _, err := coldMetadata(raw); err == nil {
			t.Fatal("ambiguous or unbounded metadata admitted")
		}
	}
	if got, err := coldMetadata(`{ "z": [1,null,true], "a": {"v":1.5} }`); err != nil || got != `{"a":{"v":1.5},"z":[1,null,true]}` {
		t.Fatal("canonical metadata semantics changed")
	}
}

type publicationFixture struct {
	f      authorityFixture
	target *ColdB0Target
	spec   ColdTransitionSpec
	intent string
	plan   *OwnershipPlan
}

func realPublication(t *testing.T) publicationFixture {
	return realPublicationMetadata(t, "{}")
}

func realPublicationMetadata(t *testing.T, metadata string) publicationFixture {
	return realPublicationSource(t, metadata, strings.Repeat("a", 40))
}

func realPublicationSource(t *testing.T, metadata, source string) publicationFixture {
	return realPublicationSeed(t, metadata, source, true)
}

func realPublicationSeed(t *testing.T, metadata, source string, seed bool) publicationFixture {
	return realPublicationSeedWithPrior(t, metadata, source, seed, false)
}

func realPublicationSeedWithPrior(t *testing.T, metadata, source string, seed, priorNative bool) publicationFixture {
	return realPublicationSeedWithPriorSpec(t, metadata, source, seed, priorNative, nil)
}
func realPublicationSeedWithPriorSpec(t *testing.T, metadata, source string, seed, priorNative bool, decorate func(*ColdTransitionSpec)) publicationFixture {
	t.Helper()
	f := greenhouseAuthorityFixture(t)
	ctx := context.Background()
	id := ordinaryID(t)
	_, err := f.observer.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,scrape_interval_hours,
 throttle_key,monitor_needs_browser,scraper_needs_browser)
 VALUES($1::uuid,$2::uuid,'browser-use-careers','https://jobs.example.test/careers','api_sniffer',$3::jsonb,60,24,'',false,true)`, id, f.company, metadata)
	if err != nil {
		t.Fatal("private B0 canonical board unavailable")
	}
	t.Cleanup(func() { _, _ = f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", id) })
	config := map[string]string{"board_slug": "browser-use-careers", "board_url": "https://jobs.example.test/careers", "crawler_type": "api_sniffer", "company_id": f.company, "metadata": metadata, "check_interval_minutes": "60", "scrape_interval_hours": "24", "throttle_key": "", "domain": "jobs.example.test", "monitor_needs_browser": "0", "scraper_needs_browser": "1"}
	if err := f.client.redis.HSet(ctx, "board:"+id, config).Err(); err != nil {
		t.Fatal(err)
	}
	lua, err := os.ReadFile("../../src/lua/lightpanda_b0_queue.lua")
	if err != nil {
		t.Fatal(err)
	}
	target, err := CaptureColdB0Target(ctx, f.observer, f.client, f.epoch, "ordinary-joint-fixture", "lightpanda-b0", "c1", lua)
	if err != nil {
		t.Fatalf("capture: %v", err)
	}
	var previous *OwnershipPlan
	if priorNative {
		previous = stageFixturePlan(t, f, strings.Repeat("b", 40))
		activateFixturePlan(t, f, previous)
		if err := f.client.redis.Set(ctx, ownershipProjectionKey, previous.body, 0).Err(); err != nil {
			t.Fatal(err)
		}
	}
	prepared := stageFixturePlan(t, f, source)
	s := coldSpec(t, f, previous, prepared)
	s.TargetB0ManifestSHA256 = target.digest
	if decorate != nil {
		decorate(&s)
	}
	intent, err := BeginColdOwnershipTransition(ctx, f.observer, f.client, s)
	if err != nil {
		t.Fatal(err)
	}
	plan, err := ReserveColdOwnershipEpoch(ctx, f.observer, f.client, intent, s.SourceRevision)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.digest)
	})
	if seed {
		seedPublicationB0(t, f.client, target, plan.Epoch())
	}
	return publicationFixture{f, target, s, intent, plan}
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

func publicationPhase(t *testing.T, p publicationFixture) string {
	t.Helper()
	var phase string
	if err := p.f.observer.QueryRow(context.Background(), "SELECT phase FROM crawler_ownership_transition WHERE intent_sha256=$1", p.intent).Scan(&phase); err != nil {
		t.Fatal(err)
	}
	return phase
}
func unchangedPublicationData(t *testing.T, p publicationFixture, before map[string]string, canonical string) {
	t.Helper()
	after := snapshot(t, p.f.client)
	delete(after, ownershipProjectionKey)
	delete(after, coldPublicationKey)
	if !reflect.DeepEqual(before, after) || coldCanonicalSnapshot(t, p.f) != canonical {
		t.Fatal("publication replayed or altered tasks/config/rows/deadlines/receipts")
	}
}

func TestRealColdPublicationPersistenceActivationAndExactRetry(t *testing.T) {
	p := realPublication(t)
	ctx := context.Background()
	before := snapshot(t, p.f.client)
	canonical := coldCanonicalSnapshot(t, p.f)
	if _, err := ActivateColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("unpublished reservation activated")
	}
	if err := PrepareColdOwnershipPublication(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	if publicationPhase(t, p) != "publishing" {
		t.Fatal("pending witness not journalled before effects")
	}
	if n, err := p.f.client.redis.Exists(ctx, ownershipProjectionKey).Result(); err != nil || n != 0 {
		t.Fatal("preparation selected ordinary projection")
	}
	for i := 0; i < 2; i++ {
		plan, err := PublishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target)
		if err != nil || plan.digest != p.plan.digest {
			t.Fatalf("publish: %v", err)
		}
	}
	if publicationPhase(t, p) != "published" {
		t.Fatal("persisted publication missing")
	}
	// A no-save Redis shutdown and fresh owned process prove that the actual
	// RDB contains BOTH routing bytes and the audited B0 records/guards.
	for reload := 0; reload < 2; reload++ {
		restartPublicationRedisWithoutSave(t, p.f.client)
		unchangedPublicationData(t, p, before, canonical)
	}
	for i := 0; i < 2; i++ {
		plan, err := ActivateColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target)
		if err != nil || plan.digest != p.plan.digest {
			t.Fatalf("activate: %v", err)
		}
	}
	if publicationPhase(t, p) != "active" {
		t.Fatal("activation not committed")
	}
	owner, err := OpenJointOwnedAuthority(ctx, p.f.dsn, p.f.client, p.plan.Epoch(), p.plan.digest, p.spec.SourceRevision, []byte(p.target.lua))
	if err != nil {
		t.Fatal("installed ordinary owner cannot attest published identity")
	}
	defer owner.Close()
	if err := owner.AttestOwnershipProjection(ctx, p.plan.ProjectionSHA1()); err != nil {
		t.Fatal(err)
	}
	unchangedPublicationData(t, p, before, canonical)
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

func TestRealColdPublicationContainsSaveFailureAndLostRedisWitness(t *testing.T) {
	for _, loss := range []bool{false, true} {
		t.Run(strconv.FormatBool(loss), func(t *testing.T) {
			p := realPublication(t)
			ctx := context.Background()
			before := snapshot(t, p.f.client)
			canonical := coldCanonicalSnapshot(t, p.f)
			if err := p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "-save").Err(); err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = p.f.client.redis.Do(context.Background(), "ACL", "SETUSER", "default", "+save").Err() })
			if _, err := PublishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); !errors.Is(err, ErrObservation) {
				t.Fatal("failed persistence admitted publication")
			}
			if publicationPhase(t, p) != "publishing" {
				t.Fatal("save failure lost recovery journal")
			}
			if _, err := ActivateColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("unpersisted projection activated")
			}
			if err := p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "+save").Err(); err != nil {
				t.Fatal(err)
			}
			if loss {
				if err := p.f.client.redis.Del(ctx, coldPublicationKey, ownershipProjectionKey).Err(); err != nil {
					t.Fatal(err)
				}
				beforeLost := snapshot(t, p.f.client)
				if _, err := PublishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); !errors.Is(err, ErrAuthorityLost) {
					t.Fatal("lost Redis publication was blindly reconstructed")
				}
				if !reflect.DeepEqual(beforeLost, snapshot(t, p.f.client)) {
					t.Fatal("contained loss changed Redis")
				}
			} else {
				if _, err := PublishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err != nil {
					t.Fatal("exact save recovery failed")
				}
				unchangedPublicationData(t, p, before, canonical)
			}
		})
	}
}

func TestRealColdPublicationRecoversDatabaseCommitSeamWithoutQueueReplay(t *testing.T) {
	p := realPublication(t)
	ctx := context.Background()
	before := snapshot(t, p.f.client)
	canonical := coldCanonicalSnapshot(t, p.f)
	if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_transition ADD CONSTRAINT private_publication_crash CHECK (phase <> 'published')"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "ALTER TABLE crawler_ownership_transition DROP CONSTRAINT IF EXISTS private_publication_crash")
	})
	if _, err := PublishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err == nil {
		t.Fatal("failed journal commit admitted publication")
	}
	if publicationPhase(t, p) != "publishing" {
		t.Fatal("publication seam lost durable attempt phase")
	}
	if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_transition DROP CONSTRAINT private_publication_crash"); err != nil {
		t.Fatal(err)
	}
	if _, err := PublishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal("exact publication recovery failed")
	}
	unchangedPublicationData(t, p, before, canonical)
}

func TestRealColdPublicationRejectsStaleB0AndDriftBeforeProjectionEffects(t *testing.T) {
	for _, mode := range []string{"epoch", "selectors", "record", "canonical", "redis_config", "witness_type", "projection_type"} {
		t.Run(mode, func(t *testing.T) {
			p := realPublication(t)
			ctx := context.Background()
			switch mode {
			case "epoch":
				_ = p.f.client.redis.HSet(ctx, p.target.keys()[0], "routing_epoch", p.plan.Epoch()-1).Err()
			case "selectors":
				_ = p.f.client.redis.HSet(ctx, "lightpanda-b0:producer-owner", "board_slug:foreign-careers", "1").Err()
			case "record":
				_ = p.f.client.redis.HSet(ctx, p.target.keys()[1], "unknown", "secret").Err()
			case "canonical":
				_, _ = p.f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", p.target.document.Boards[0].ID)
			case "redis_config":
				_ = p.f.client.redis.HSet(ctx, "board:"+p.target.document.Boards[0].ID, "scrape_interval_hours", "25").Err()
			case "witness_type":
				_ = p.f.client.redis.HSet(ctx, coldPublicationKey, "unknown", "secret").Err()
			case "projection_type":
				_ = p.f.client.redis.HSet(ctx, ownershipProjectionKey, "unknown", "secret").Err()
			}
			before := snapshot(t, p.f.client)
			if _, err := PublishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err == nil || strings.Contains(err.Error(), "secret") {
				t.Fatal("bad B0/profile/Redis state admitted or leaked")
			}
			if publicationPhase(t, p) != "reserved" || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
				t.Fatal("invalid publication changed routing")
			}
		})
	}
}

func TestRealColdPublicationActivationRollbackPreservesStagedOwner(t *testing.T) {
	p := realPublication(t)
	ctx := context.Background()
	if _, err := PublishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_transition ADD CONSTRAINT private_activation_crash CHECK (phase <> 'active')"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "ALTER TABLE crawler_ownership_transition DROP CONSTRAINT IF EXISTS private_activation_crash")
	})
	if _, err := ActivateColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err == nil {
		t.Fatal("failed active commit admitted owner")
	}
	var state string
	if err := p.f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", p.plan.digest).Scan(&state); err != nil || state != "staged" || publicationPhase(t, p) != "published" {
		t.Fatal("activation failure leaked owner or lost publication")
	}
	if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_transition DROP CONSTRAINT private_activation_crash"); err != nil {
		t.Fatal(err)
	}
	if _, err := ActivateColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal("exact activation retry failed")
	}
}

func TestRealColdPublicationSuccessorBindsExactActiveGeneration(t *testing.T) {
	p := realPublication(t)
	ctx := context.Background()
	if _, err := PublishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	if _, err := ActivateColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	a, err := OpenAuthority(ctx, p.f.dsn, p.f.client, p.plan.Epoch())
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	f := p.f
	f.authority = a
	f.epoch = p.plan.Epoch()
	prepared := stageFixturePlan(t, f, strings.Repeat("b", 40))
	s := coldSpec(t, f, p.plan, prepared)
	s.TargetB0ManifestSHA256 = p.target.digest
	before := snapshot(t, p.f.client)
	if _, err := BeginColdOwnershipTransition(ctx, p.f.observer, p.f.client, s); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("unbound prior release superseded active journal")
	}
	if publicationPhase(t, p) != "active" || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
		t.Fatal("failed successor changed current authority")
	}
	s.ActiveReleaseSHA256 = p.spec.TargetReleaseSHA256
	s.RollbackReleaseSHA256 = s.ActiveReleaseSHA256
	s.TargetReleaseSHA256 = strings.Repeat("6", 64)
	intent, err := BeginColdOwnershipTransition(ctx, p.f.observer, p.f.client, s)
	if err != nil {
		t.Fatal("exact successor intent rejected")
	}
	if publicationPhase(t, p) != "superseded" {
		t.Fatal("previous generation not retained as superseded")
	}
	plan, err := ReserveColdOwnershipEpoch(ctx, p.f.observer, p.f.client, intent, s.SourceRevision)
	if err != nil {
		t.Fatal(err)
	}
	var phase string
	if _, err := PublishColdOwnership(ctx, p.f.observer, p.f.client, intent, s.SourceRevision, p.target); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("successor adopted old B0 epoch instead of requiring transfer")
	}
	if err := p.f.observer.QueryRow(ctx, "SELECT phase FROM crawler_ownership_transition WHERE intent_sha256=$1", intent).Scan(&phase); err != nil || phase != "reserved" || plan.Epoch() != p.plan.Epoch()+1 || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
		t.Fatal("stale B0 successor changed publication")
	}
}
