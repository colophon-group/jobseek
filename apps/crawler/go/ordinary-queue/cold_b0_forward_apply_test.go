package queue

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

func coldForwardBytesHash(body []byte) string {
	h := sha256.Sum256(body)
	return hex.EncodeToString(h[:])
}

func TestColdB0ForwardCompletionSchemaMatchesAppliedMigration(t *testing.T) {
	body, err := os.ReadFile("../../src/migrations/sql/crawler_ownership_b0_forward_completion.sql")
	if err != nil || string(body) != coldB0ForwardCompletionSchema {
		t.Fatal("completion differs from applied migration")
	}
}

// This private adapter preserves opaque preparation decisions but uses the
// actual unchanged Lua for every effect and the shared real task codec. It is
// not proof of producer parser assignment; that belongs to the root fixture.
type forwardLuaControl struct {
	*forwardFixtureControl
	activations int
	loseReply   bool
	afterEffect func()
}

func (f *forwardLuaControl) wanted(ctx context.Context, t b0producer.Task) (b0task.Task, string, error) {
	p := f.p
	previous := ""
	if raw, err := p.f.client.redis.HGet(ctx, p.target.keys()[1], t.PostingID).Result(); err == nil {
		var r coldB0RollbackRecord
		if json.Unmarshal([]byte(raw), &r) != nil {
			return b0task.Task{}, "", ErrProtocol
		}
		previous = r.PayloadSHA256
		if r.State == "ready" {
			task, err := b0task.Decode(r.Payload, r.PayloadSHA256, b0task.Route{ShardID: p.target.document.ShardID, RoutingEpoch: p.plan.Epoch(), EngineOwner: "go"})
			return task, previous, err
		}
	} else if err != redis.Nil {
		return b0task.Task{}, "", err
	}
	body, err := os.ReadFile("../../contracts/v1/b0task/testdata/python_tasks.json")
	if err != nil {
		return b0task.Task{}, "", err
	}
	var corpus struct {
		Cases []struct {
			Payload string `json:"payload"`
		} `json:"cases"`
	}
	if json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) == 0 {
		return b0task.Task{}, "", ErrProtocol
	}
	var envelope map[string]any
	if json.Unmarshal([]byte(corpus.Cases[0].Payload), &envelope) != nil {
		return b0task.Task{}, "", ErrProtocol
	}
	revision := int64(1)
	if previous != "" {
		raw, _ := p.f.client.redis.HGet(ctx, p.target.keys()[1], t.PostingID).Result()
		var r coldB0RollbackRecord
		_ = json.Unmarshal([]byte(raw), &r)
		old, err := b0task.Decode(r.Payload, r.PayloadSHA256, b0task.Route{ShardID: p.target.document.ShardID, RoutingEpoch: p.plan.Epoch(), EngineOwner: "go"})
		if err != nil {
			return b0task.Task{}, "", err
		}
		revision = old.Envelope.ConfigRevision + 1
	}
	envelope["task_id"], envelope["board_id"], envelope["source_url"], envelope["domain"] = t.PostingID, t.Config["board_id"], t.Config["source_url"], t.Domain
	envelope["routing_epoch"], envelope["shard_id"], envelope["config_revision"], envelope["initial_ready_at_ms"] = p.plan.Epoch(), p.target.document.ShardID, revision, t.NextScrapeAtMS
	payload, err := b0task.CanonicalJSON(envelope, false)
	if err != nil {
		return b0task.Task{}, "", err
	}
	digest := coldForwardBytesHash(payload)
	task, err := b0task.Decode(string(payload), digest, b0task.Route{ShardID: p.target.document.ShardID, RoutingEpoch: p.plan.Epoch(), EngineOwner: "go"})
	return task, previous, err
}

func (f *forwardLuaControl) Prepare(ctx context.Context, t b0producer.Task) (b0producer.Response, error) {
	r, err := f.forwardFixtureControl.Prepare(ctx, t)
	if err != nil {
		return r, err
	}
	task, _, err := f.wanted(ctx, t)
	r.PayloadSHA256 = task.PayloadSHA256
	return r, err
}

func (f *forwardLuaControl) Activate(ctx context.Context, t b0producer.Task, digest string) (b0producer.Response, error) {
	f.activations++
	r, err := f.Prepare(ctx, t)
	if err != nil || r.PreparationDigest != digest {
		return r, ErrAuthorityLost
	}
	task, previous, err := f.wanted(ctx, t)
	if err != nil {
		return r, err
	}
	config := map[string]string{}
	for k, v := range t.Config {
		config[k] = v
	}
	if t.LegacyScheduleScore != "" {
		config["__operator_source_score"] = t.LegacyScheduleScore
	}
	body, _ := json.Marshal(config)
	args := f.p.target.auditArguments(f.p.plan.Epoch())
	args[0], args[4], args[5], args[8] = "activate_legacy", t.PostingID, strconv.FormatInt(task.Envelope.ConfigRevision, 10), strconv.FormatInt(t.NextScrapeAtMS, 10)
	args[10], args[11], args[12], args[15] = task.Payload, task.PayloadSHA256, task.PayloadSHA1, previous
	args[18], args[19] = string(body), "1"
	args[22] = "0"
	if t.FirstTime {
		args[22] = "1"
	}
	reply, err := f.p.f.client.redis.Eval(ctx, f.p.target.lua, f.p.target.keys(), args...).Slice()
	if err != nil || len(reply) != 12 || reply[0] != "accepted" {
		return r, ErrAuthorityLost
	}
	if f.afterEffect != nil {
		callback := f.afterEffect
		f.afterEffect = nil
		callback()
	}
	if f.loseReply {
		f.loseReply = false
		return b0producer.Response{}, b0producer.ErrUnavailable
	}
	r.Outcome, r.Activated = "activated", reply[1] != "already_activated"
	return r, nil
}

func retainedForwardApplication(t *testing.T, terminal bool) (publicationFixture, *ColdB0ForwardPlan, *forwardLuaControl) {
	t.Helper()
	p, r, opaque := forwardFixture(t)
	if terminal {
		rollbackFixtureState(t, p, "terminal")
		if ttl, err := p.f.client.redis.PTTL(context.Background(), "ratelimit:jobs.example.test").Result(); err != nil || ttl <= 0 {
			t.Fatal("actual private source rate bucket absent")
		}
		if err := p.f.client.redis.Persist(context.Background(), "ratelimit:jobs.example.test").Err(); err != nil {
			t.Fatal("private source rate baseline not frozen")
		}
	}
	forwardFixtureLegacyPosting(t, p, true, "350.0001", true)
	forwardFixtureLegacyPosting(t, p, false, "1925089445.123001", true)
	forwardFixtureLegacyPosting(t, p, false, "0", false)
	control := &forwardLuaControl{forwardFixtureControl: opaque}
	var plan *ColdB0ForwardPlan
	err := coldTransitionTransaction(context.Background(), p.f.observer, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		plan, err = deriveColdB0ForwardPlan(ctx, tx, p.f.client, control, r, p.target)
		if err != nil {
			return err
		}
		plan, err = retainColdB0ForwardPlan(ctx, tx, p.f.client, control, r, p.target, plan.digest)
		return err
	})
	if err != nil {
		t.Fatal("full approval not retained", err)
	}
	return p, plan, control
}

func TestRealColdB0ForwardApplicationRetainsSaveReadbackAndSkipsCompleted(t *testing.T) {
	for _, terminal := range []bool{false, true} {
		t.Run(strconv.FormatBool(terminal), func(t *testing.T) {
			p, plan, control := retainedForwardApplication(t, terminal)
			ctx := context.Background()
			canonical := coldB0CanonicalSnapshot(t, p)
			state, err := applyRetainedColdB0Forward(ctx, p.f.observer, p.f.client, control, plan.digest, plan.Request().SourceRevision, p.target)
			if err != nil || state.Phase() != "redis-transferred" || state.Plan().digest != plan.digest || !ownershipSHA256.MatchString(state.ReceiptSHA256()) {
				t.Fatal("retained forward application failed", err)
			}
			if canonical != coldB0CanonicalSnapshot(t, p) || publicationPhase(t, p) != "reserved" {
				t.Fatal("application selected ownership/replayed canonical data")
			}
			calls := control.activations
			before := forwardRedisSnapshot(t, p.f.client)
			restartPublicationRedisWithoutSave(t, p.f.client)
			if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) {
				t.Fatal("SAVE lost actual forward state")
			}
			repeated, err := applyRetainedColdB0Forward(ctx, p.f.observer, p.f.client, control, plan.digest, plan.Request().SourceRevision, p.target)
			if err != nil || repeated.body != state.body || control.activations != calls || !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) {
				t.Fatal("completed retry replayed/lost persisted evidence", err)
			}
			observed, err := InspectColdB0ForwardApplication(ctx, p.f.observer, plan.digest, plan.Request().SourceRevision)
			if err != nil || observed.body != state.body {
				t.Fatal("immutable application inspection lost receipt", err)
			}
			for _, statement := range []string{"UPDATE crawler_ownership_b0_forward_completion SET payload=payload WHERE plan_sha256=$1", "DELETE FROM crawler_ownership_b0_forward_completion WHERE plan_sha256=$1"} {
				if _, err := p.f.observer.Exec(ctx, statement, plan.digest); err == nil {
					t.Fatal("completion history rewritable")
				}
			}
		})
	}
}

func TestRealColdB0ForwardApplicationRecoversPartialUncertainReplyWithoutReplay(t *testing.T) {
	p, plan, control := retainedForwardApplication(t, false)
	control.loseReply = true
	ctx := context.Background()
	canonical := coldB0CanonicalSnapshot(t, p)
	if _, err := applyRetainedColdB0Forward(ctx, p.f.observer, p.f.client, control, plan.digest, plan.Request().SourceRevision, p.target); !errors.Is(err, ErrObservation) || control.activations != 1 {
		t.Fatal("uncertain reply silently retried", err)
	}
	state, err := InspectColdB0ForwardApplication(ctx, p.f.observer, plan.digest, plan.Request().SourceRevision)
	if err != nil || state.Phase() != "prepared" {
		t.Fatal("partial transfer claimed completion")
	}
	if _, err := applyRetainedColdB0Forward(ctx, p.f.observer, p.f.client, control, plan.digest, plan.Request().SourceRevision, p.target); err != nil || control.activations != len(plan.document.Tasks) {
		t.Fatal("partial recovery replayed completed transfer", err)
	}
	if canonical != coldB0CanonicalSnapshot(t, p) {
		t.Fatal("partial recovery replayed canonical work")
	}
}

func TestRealColdB0ForwardApplicationSaveAndSQLFailureRemainContained(t *testing.T) {
	for _, fault := range []string{"save_denied", "sql_after_save", "late_source"} {
		t.Run(fault, func(t *testing.T) {
			p, plan, control := retainedForwardApplication(t, false)
			ctx := context.Background()
			name := "forward_completion_" + strings.ReplaceAll(p.f.company, "-", "")
			quoted := pgx.Identifier{name}.Sanitize()
			if fault == "save_denied" {
				if p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "-save").Err() != nil {
					t.Fatal("private SAVE denial unavailable")
				}
				t.Cleanup(func() { _ = p.f.client.redis.Do(context.Background(), "ACL", "SETUSER", "default", "+save").Err() })
			} else if fault == "sql_after_save" {
				_, err := p.f.observer.Exec(ctx, "CREATE FUNCTION "+quoted+"() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private_receipt_failure'; END $$")
				if err != nil {
					t.Fatal(err)
				}
				_, err = p.f.observer.Exec(ctx, "CREATE TRIGGER "+quoted+" BEFORE INSERT ON crawler_ownership_b0_forward_completion FOR EACH ROW EXECUTE FUNCTION "+quoted+"()")
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					_, _ = p.f.observer.Exec(context.Background(), "DROP TRIGGER IF EXISTS "+quoted+" ON crawler_ownership_b0_forward_completion")
					_, _ = p.f.observer.Exec(context.Background(), "DROP FUNCTION "+quoted+"()")
				})
			} else {
				control.afterEffect = func() {
					_ = p.f.client.redis.HSet(ctx, "scrape:"+plan.document.Tasks[0].PostingID, "description_r2_hash", "9").Err()
				}
			}
			if _, err := applyRetainedColdB0Forward(ctx, p.f.observer, p.f.client, control, plan.digest, plan.Request().SourceRevision, p.target); err == nil {
				t.Fatal("failed completion granted success")
			}
			if state, err := InspectColdB0ForwardApplication(ctx, p.f.observer, plan.digest, plan.Request().SourceRevision); err != nil || state.Phase() != "prepared" || publicationPhase(t, p) != "reserved" {
				t.Fatal("failed effects released ownership")
			}
			calls := control.activations
			if fault == "save_denied" {
				if p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "+save").Err() != nil {
					t.Fatal("private SAVE restore failed")
				}
			}
			if fault == "sql_after_save" {
				if _, err := p.f.observer.Exec(ctx, "DROP TRIGGER "+quoted+" ON crawler_ownership_b0_forward_completion"); err != nil {
					t.Fatal(err)
				}
				restartPublicationRedisWithoutSave(t, p.f.client)
			}
			if fault != "late_source" {
				if _, err := applyRetainedColdB0Forward(ctx, p.f.observer, p.f.client, control, plan.digest, plan.Request().SourceRevision, p.target); err != nil || control.activations != calls {
					t.Fatal("completion recovery replayed transferred source", err)
				}
			} else if _, err := applyRetainedColdB0Forward(ctx, p.f.observer, p.f.client, control, plan.digest, plan.Request().SourceRevision, p.target); !errors.Is(err, ErrAuthorityLost) || control.activations != calls {
				t.Fatal("late drift admitted replay", err)
			}
		})
	}
}

func TestRealColdB0ForwardCompletionDowngradeRetainsHistory(t *testing.T) {
	p, plan, control := retainedForwardApplication(t, false)
	ctx := context.Background()
	state, err := applyRetainedColdB0Forward(ctx, p.f.observer, p.f.client, control, plan.digest, plan.Request().SourceRevision, p.target)
	if err != nil {
		t.Fatal(err)
	}
	uv, err := exec.LookPath("uv")
	if err != nil {
		t.Fatal("required actual Alembic downgrade unavailable")
	}
	command := exec.Command(uv, "run", "--frozen", "--no-sync", "alembic", "-c", "src/migrations/alembic.ini", "downgrade", "0043")
	command.Dir = "../.."
	command.Env = append(os.Environ(), "LOCAL_DATABASE_URL="+p.f.dsn)
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "crawler_ownership_history_retained") {
		t.Fatal("actual downgrade removed completion history")
	}
	observed, err := InspectColdB0ForwardApplication(ctx, p.f.observer, plan.digest, plan.Request().SourceRevision)
	if err != nil || observed.body != state.body {
		t.Fatal("failed downgrade lost completion")
	}
}

func TestRealColdB0ForwardApplicationRejectsChangedAuthorityBeforeEffects(t *testing.T) {
	for _, fault := range []string{"wrong_digest", "wrong_source", "wrong_target", "advanced_epoch", "canonical_due", "canonical_lease", "target_config", "lost_record", "foreign_record"} {
		t.Run(fault, func(t *testing.T) {
			p, plan, control := retainedForwardApplication(t, false)
			ctx := context.Background()
			digest, source, target := plan.digest, plan.Request().SourceRevision, p.target
			var err error
			switch fault {
			case "wrong_digest":
				digest = strings.Repeat("f", 64)
			case "wrong_source":
				source = strings.Repeat("f", 40)
			case "wrong_target":
				copy := *target
				copy.digest = strings.Repeat("f", 64)
				target = &copy
			case "advanced_epoch":
				_, err = p.f.observer.Exec(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')")
			case "canonical_due":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=next_scrape_at+interval '1 hour' WHERE id=$1::uuid", plan.document.Tasks[0].PostingID)
			case "canonical_lease":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET leased_until=now()+interval '1 hour' WHERE id=$1::uuid", plan.document.Tasks[0].PostingID)
			case "target_config":
				err = p.f.client.redis.HSet(ctx, "board:"+target.document.Boards[0].ID, "metadata", `{"changed":true}`).Err()
			case "lost_record":
				for id := range plan.document.Snapshot.B0.Records {
					err = p.f.client.redis.HDel(ctx, target.keys()[1], id).Err()
					break
				}
			case "foreign_record":
				err = p.f.client.redis.HSet(ctx, target.keys()[1], ordinaryID(t), "{}").Err()
			}
			if err != nil {
				t.Fatal("private authority fault unavailable", err)
			}
			before, canonical := forwardRedisSnapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
			if _, err := applyRetainedColdB0Forward(ctx, p.f.observer, p.f.client, control, digest, source, target); err == nil || control.activations != 0 {
				t.Fatal("stale authority reached producer mutation", fault, err)
			}
			if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) || publicationPhase(t, p) != "reserved" {
				t.Fatal("rejected authority changed source")
			}
		})
	}
}

func TestRealColdB0ForwardCompletedReceiptDoesNotRepairLostSource(t *testing.T) {
	p, plan, control := retainedForwardApplication(t, false)
	ctx := context.Background()
	state, err := applyRetainedColdB0Forward(ctx, p.f.observer, p.f.client, control, plan.digest, plan.Request().SourceRevision, p.target)
	if err != nil {
		t.Fatal(err)
	}
	calls := control.activations
	if err := p.f.client.redis.HSet(ctx, "scrape:"+plan.document.Tasks[0].PostingID, "description_r2_hash", "9").Err(); err != nil {
		t.Fatal(err)
	}
	before := forwardRedisSnapshot(t, p.f.client)
	if _, err := applyRetainedColdB0Forward(ctx, p.f.observer, p.f.client, control, plan.digest, plan.Request().SourceRevision, p.target); !errors.Is(err, ErrAuthorityLost) || control.activations != calls || !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) {
		t.Fatal("lost completion witness was replayed/repaired", err)
	}
	if observed, err := InspectColdB0ForwardApplication(ctx, p.f.observer, plan.digest, plan.Request().SourceRevision); err != nil || observed.body != state.body {
		t.Fatal("historical completion was discarded", err)
	}
}
