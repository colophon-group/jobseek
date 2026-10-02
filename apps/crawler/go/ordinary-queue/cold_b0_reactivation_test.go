package queue

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
)

func TestColdB0ReactivationSchemaMatchesAppliedMigration(t *testing.T) {
	body, err := os.ReadFile("../../src/migrations/sql/crawler_ownership_b0_reactivation.sql")
	if err != nil || string(body) != coldB0ReactivationSchema {
		t.Fatal("reactivation schema differs from migration")
	}
}
func reactivationHostReceipt(epoch int64) string {
	return fmt.Sprintf("schema=jobseek.lightpanda-b0-active/v1\nstate=active\ncohort=c1\nnamespace=ordinary-joint-fixture\nshard_id=lightpanda-b0\nrouting_epoch=%d\nplan_digest=%s\ncompose_digest=%s\ncrawler_image_ref=ghcr.io/colophon-group/jobseek-crawler@sha256:%s\ndeploy_revision=%s\nactivated_at_epoch=1790812800\n", epoch, strings.Repeat("1", 64), strings.Repeat("2", 64), strings.Repeat("3", 64), strings.Repeat("b", 40))
}

func TestColdB0ReactivationReceiptRejectsMalformedHashedHostData(t *testing.T) {
	valid := reactivationHostReceipt(7)
	target := &ColdB0Target{document: coldB0Document{Cohort: "c1", Namespace: "ordinary-joint-fixture", ShardID: "lightpanda-b0"}}
	if _, err := coldB0ReactivationReceipt(valid, coldForwardBytesDigest(valid), target, 9); err != nil {
		t.Fatal("valid historical receipt refused", err)
	}
	for _, body := range []string{
		strings.TrimSuffix(valid, "\n"),
		valid + "unknown=value\n",
		strings.Replace(valid, "state=active", "state=active=extra", 1),
		strings.Replace(valid, "state=active", "state=active\r", 1),
		strings.Replace(valid, "state=active", "state=act\x00ive", 1),
		strings.Replace(valid, "state=active", "state=áctive", 1),
		strings.Replace(valid, "state=active", "schema=jobseek.lightpanda-b0-active/v1", 1),
		strings.Replace(valid, "routing_epoch=7", "routing_epoch=07", 1),
		strings.Replace(valid, "routing_epoch=7", "routing_epoch=9", 1),
		strings.Replace(valid, "namespace=ordinary-joint-fixture", "namespace=other", 1),
		strings.Replace(valid, "ghcr.io/colophon-group/", "example.test/colophon-group/", 1),
		strings.Replace(valid, "activated_at_epoch=1790812800", "activated_at_epoch=0", 1),
	} {
		if _, err := coldB0ReactivationReceipt(body, coldForwardBytesDigest(body), target, 9); !errors.Is(err, ErrAuthorityLost) {
			t.Fatal("malformed host receipt admitted despite valid raw byte hash", err)
		}
	}
}

func reactivationFixture(t *testing.T, native bool) (publicationFixture, ColdB0ReactivationRequest, *forwardLuaControl, string) {
	return reactivationFixtureWithSource(t, native, "{}", strings.Repeat("a", 40))
}

func reactivationFixtureWithSource(t *testing.T, native bool, metadata, source string) (publicationFixture, ColdB0ReactivationRequest, *forwardLuaControl, string) {
	return reactivationFixtureBootstrap(t, native, metadata, source, true)
}

func reactivationFixtureBootstrap(t *testing.T, native bool, metadata, source string, initialize bool) (publicationFixture, ColdB0ReactivationRequest, *forwardLuaControl, string) {
	t.Helper()
	ctx := context.Background()
	receipt := ""
	p := realPublicationSeedWithPriorSpec(t, metadata, source, true, native, func(s *ColdTransitionSpec) {
		receipt = reactivationHostReceipt(s.PreviousEpoch)
		s.PreviousB0ReceiptSHA256 = coldForwardBytesDigest(receipt)
	})
	rollbackFixturePosting(t, p)
	// Finish the original first-time attempt through the real Lua/SQL fence.
	// Cold rollback must restore its canonical fractional recurring due time.
	rollbackFixtureState(t, p, "completed")
	request := rollbackFixtureRequest(t, p)
	b0, err := BuildColdB0RollbackPlan(ctx, p.f.observer, p.f.client, request, p.target)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := RetainColdB0RollbackPlan(ctx, p.f.observer, p.f.client, request, p.target, b0.digest); err != nil {
		t.Fatal(err)
	}
	if _, err := RestoreColdB0Rollback(ctx, p.f.observer, p.f.client, b0.digest, request.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	ordinaryRequest := ColdOrdinaryRestorationRequest{ReversalSHA256: request.ReversalSHA256, SourceRevision: request.SourceRevision, RetirementEpoch: request.RetirementEpoch, B0RestorationPlanSHA256: b0.digest}
	if native {
		ordinaryRequest.RollbackSourceRevision = strings.Repeat("b", 40)
	}
	ordinary, err := BuildColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, ordinaryRequest, p.target)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := RetainColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, ordinaryRequest, p.target, ordinary.digest); err != nil {
		t.Fatal(err)
	}
	// Use the unchanged real tombstone clearing/producer initialization Lua.
	// The fixture does not claim host sentinel removal or actual producer parsing.
	args := p.target.auditArguments(request.B0SourceEpoch)
	args[0], args[15], args[22] = "clear_rollback_tombstone", b0.digest, request.SourceReceiptSHA256
	reply, err := p.f.client.redis.Eval(ctx, p.target.lua, p.target.keys(), args...).Slice()
	if err != nil || len(reply) != 12 || reply[0] != "accepted" {
		t.Fatal("actual tombstone clear rejected", err, reply)
	}
	if initialize {
		args = p.target.auditArguments(request.RetirementEpoch)
		args[0] = "initialize_producer"
		reply, err = p.f.client.redis.Eval(ctx, p.target.lua, p.target.keys(), args...).Slice()
		if err != nil || len(reply) != 12 || reply[0] != "accepted" {
			t.Fatal("actual R initialization rejected", err, reply)
		}
	}
	current := p
	current.plan = &OwnershipPlan{document: ownershipDocument{Epoch: request.RetirementEpoch}}
	control := &forwardLuaControl{forwardFixtureControl: &forwardFixtureControl{p: current}}
	return p, ColdB0ReactivationRequest{ordinary.digest, request.SourceRevision}, control, receipt
}
func retainedReactivation(t *testing.T, native bool) (publicationFixture, *ColdB0ReactivationPlan, *forwardLuaControl) {
	t.Helper()
	p, r, control, receipt := reactivationFixture(t, native)
	ctx := context.Background()
	before, canonical := forwardRedisSnapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
	plan, err := buildColdB0ReactivationPlan(ctx, p.f.observer, p.f.client, control, r, p.target, receipt)
	if err != nil {
		t.Fatal("reactivation preview failed", err)
	}
	for i := 0; i < 2; i++ {
		retained, err := retainColdB0ReactivationPlan(ctx, p.f.observer, p.f.client, control, r, p.target, receipt, plan.digest)
		if err != nil || retained.body != plan.body {
			t.Fatal("exact reactivation retention failed", err)
		}
	}
	if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
		t.Fatal("reactivation preparation changed source data")
	}
	return p, plan, control
}
func TestRealColdB0ReactivationRestoresPriorGoQueuesAtReservedRetirement(t *testing.T) {
	for _, native := range []bool{false, true} {
		t.Run(strconv.FormatBool(native), func(t *testing.T) {
			ctx := context.Background()
			p, plan, control := retainedReactivation(t, native)
			canonical := coldB0CanonicalSnapshot(t, p)
			state, err := applyColdB0Reactivation(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target)
			if err != nil || state.Phase() != "redis-reactivated" || !ownershipSHA256.MatchString(state.digest) {
				t.Fatal("actual R transfer failed", err)
			}
			before := forwardRedisSnapshot(t, p.f.client)
			calls := control.activations
			for i := 0; i < 2; i++ {
				retry, err := applyColdB0Reactivation(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target)
				if err != nil || retry.digest != state.digest || control.activations != calls {
					t.Fatal("completed R transfer replayed", err)
				}
			}
			if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) || publicationPhase(t, p) != "reversing" {
				t.Fatal("reactivation changed canonical/ordinary ownership")
			}
			var epoch int64
			var active int
			if err := p.f.observer.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epoch); err != nil || epoch != plan.document.RetirementEpoch {
				t.Fatal("reactivation allocated R+1", err)
			}
			if err := p.f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_ownership_plan WHERE state='active'").Scan(&active); err != nil || active != 0 {
				t.Fatal("reactivation activated ordinary owner", err)
			}
			for _, task := range plan.forward.document.Tasks {
				// Redis versions render the same binary64 ZSET score differently.
				// Preserve the exact retained source bytes rather than requiring a
				// platform-specific decimal spelling of the canonical PG schedule.
				// LegacyScheduleScore is the normalized producer request field;
				// Snapshot.Legacy retains Redis's original ZSCORE bytes.
				score := ""
				for index, row := range plan.forward.document.PostgresRows {
					if row.ID == task.PostingID {
						score = plan.forward.document.Snapshot.Legacy[index].Scores[3]
					}
				}
				if millis, err := coldB0ForwardMillis(score); err != nil || millis != 1925089445101 || task.NextScrapeAtMS != millis || task.FirstTime {
					t.Fatal("restored canonical fractional schedule changed", score, err)
				}
				if guard := p.f.client.redis.HGet(ctx, "lightpanda-b0:legacy-guard", task.PostingID).Val(); !strings.Contains(guard, "|"+strconv.FormatInt(epoch, 10)+"|") || !strings.HasSuffix(guard, "|"+score) {
					t.Fatal("fresh R guard lost exact retained fractional source score", guard)
				}
			}
			restartPublicationRedisWithoutSave(t, p.f.client)
			if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) {
				t.Fatal("acknowledged reactivation SAVE lost R queue")
			}
			retry, err := applyColdB0Reactivation(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target)
			if err != nil || retry.digest != state.digest || control.activations != calls {
				t.Fatal("RDB retry replayed transferred tasks", err)
			}
		})
	}
}
func TestRealColdB0ReactivationRecoversUncertainEffectsAndSQLCommitSeams(t *testing.T) {
	for _, fault := range []string{"lost_reply", "save_denied", "sql_after_save"} {
		t.Run(fault, func(t *testing.T) {
			ctx := context.Background()
			p, plan, control := retainedReactivation(t, true)
			canonical := coldB0CanonicalSnapshot(t, p)
			if fault == "lost_reply" {
				control.loseReply = true
			}
			if fault == "save_denied" {
				if err := p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "-save").Err(); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = p.f.client.redis.Do(context.Background(), "ACL", "SETUSER", "default", "+save").Err() })
			}
			if fault == "sql_after_save" {
				if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_b0_reactivation_completion ADD CONSTRAINT private_reactivation_failure CHECK(false) NOT VALID"); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					_, _ = p.f.observer.Exec(context.Background(), "ALTER TABLE crawler_ownership_b0_reactivation_completion DROP CONSTRAINT IF EXISTS private_reactivation_failure")
				})
			}
			if _, err := applyColdB0Reactivation(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err == nil {
				t.Fatal("uncertain/failed completion admitted")
			}
			state, err := InspectColdB0ReactivationApplication(ctx, p.f.observer, plan.digest, p.spec.SourceRevision)
			if err != nil || state.Phase() != "prepared" {
				t.Fatal("SQL history claimed uncommitted completion", err)
			}
			before := forwardRedisSnapshot(t, p.f.client)
			calls := control.activations
			if fault == "save_denied" {
				if err := p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "+save").Err(); err != nil {
					t.Fatal(err)
				}
			}
			if fault == "sql_after_save" {
				restartPublicationRedisWithoutSave(t, p.f.client)
				if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) {
					t.Fatal("SAVE-to-SQL crash lost transferred state")
				}
				if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_b0_reactivation_completion DROP CONSTRAINT private_reactivation_failure"); err != nil {
					t.Fatal(err)
				}
			}
			state, err = applyColdB0Reactivation(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target)
			if err != nil || state.Phase() != "redis-reactivated" || control.activations != calls || !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
				t.Fatal("explicit retry changed original R effects", err)
			}
		})
	}
}
func TestRealColdB0ReactivationRejectsDriftAndDoesNotRepairLostCompletion(t *testing.T) {
	for _, fault := range []string{"wrong_digest", "source", "advanced_epoch", "canonical_due", "live_lease", "ordinary_config", "producer_cohort", "projection", "marker", "lost_record"} {
		t.Run(fault, func(t *testing.T) {
			ctx := context.Background()
			p, plan, control := retainedReactivation(t, true)
			if fault == "lost_record" {
				if _, err := applyColdB0Reactivation(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
					t.Fatal(err)
				}
			}
			digest, source := plan.digest, p.spec.SourceRevision
			var err error
			switch fault {
			case "wrong_digest":
				digest = strings.Repeat("0", 64)
			case "source":
				source = strings.Repeat("c", 40)
			case "advanced_epoch":
				_, err = p.f.observer.Exec(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')")
			case "canonical_due":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=next_scrape_at+interval '1 hour' WHERE id=$1::uuid", plan.forward.document.Tasks[0].PostingID)
			case "live_lease":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET leased_until=now()+interval '1 hour' WHERE id=$1::uuid", plan.forward.document.Tasks[0].PostingID)
			case "ordinary_config":
				err = p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "check_interval_minutes", "17").Err()
			case "producer_cohort":
				err = p.f.client.redis.HSet(ctx, "lightpanda-b0:producer-owner", "cohort", "c2").Err()
			case "projection":
				err = p.f.client.redis.Set(ctx, ownershipProjectionKey, "unapproved", 0).Err()
			case "marker":
				err = p.f.client.redis.Set(ctx, coldPublicationKey, "unapproved", 0).Err()
			case "lost_record":
				err = p.f.client.redis.HDel(ctx, p.target.keys()[1], plan.forward.document.Tasks[0].PostingID).Err()
			}
			if err != nil {
				t.Fatal(err)
			}
			before, canonical, calls := forwardRedisSnapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p), control.activations
			if _, err := applyColdB0Reactivation(ctx, p.f.observer, p.f.client, control, digest, source, p.target); err == nil {
				t.Fatal("drift admitted")
			}
			if control.activations != calls || !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
				t.Fatal("drift refusal replayed/repaired data")
			}
		})
	}
}
func TestRealColdB0ReactivationHistoryInspectionAndDowngradeRetainIdentity(t *testing.T) {
	ctx := context.Background()
	p, plan, control := retainedReactivation(t, true)
	state, err := applyColdB0Reactivation(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target)
	if err != nil {
		t.Fatal(err)
	}
	for _, table := range []string{"crawler_ownership_b0_reactivation", "crawler_ownership_b0_reactivation_completion"} {
		for _, query := range []string{"UPDATE " + table + " SET created_at=created_at WHERE plan_sha256=$1", "DELETE FROM " + table + " WHERE plan_sha256=$1"} {
			if _, err := p.f.observer.Exec(ctx, query, plan.digest); err == nil {
				t.Fatal("retained history mutated")
			}
		}
	}
	uv, err := exec.LookPath("uv")
	if err != nil {
		t.Fatal("required actual Alembic unavailable")
	}
	command := exec.Command(uv, "run", "--frozen", "--no-sync", "alembic", "-c", "src/migrations/alembic.ini", "downgrade", "0045")
	command.Dir = "../.."
	command.Env = append(os.Environ(), "LOCAL_DATABASE_URL="+p.f.dsn)
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "crawler_ownership_history_retained") {
		t.Fatal("actual downgrade discarded reactivation history")
	}
	if _, err := p.f.observer.Exec(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')"); err != nil {
		t.Fatal(err)
	}
	tx, err := p.f.observer.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1),pg_advisory_xact_lock($2)", OrdinaryLeaseBarrier, routingEpochBarrier); err != nil {
		t.Fatal(err)
	}
	inspected, err := InspectColdB0ReactivationApplication(ctx, p.f.observer, plan.digest, p.spec.SourceRevision)
	if err != nil || inspected.body != state.body || inspected.plan.body != plan.body {
		t.Fatal("inspection adopted allocator or waited for barriers", err)
	}
}
func TestRealColdB0ReactivationRejectsChangedHistoricalReceiptAndNoncanonicalPlan(t *testing.T) {
	p, r, control, receipt := reactivationFixture(t, true)
	ctx := context.Background()
	plan, err := buildColdB0ReactivationPlan(ctx, p.f.observer, p.f.client, control, r, p.target, receipt)
	if err != nil {
		t.Fatal(err)
	}
	for _, bad := range []string{receipt + "\n", strings.Replace(receipt, "state=active", "state=pending", 1), strings.Replace(receipt, "deploy_revision="+strings.Repeat("b", 40), "deploy_revision="+strings.Repeat("c", 40), 1)} {
		if _, err := buildColdB0ReactivationPlan(ctx, p.f.observer, p.f.client, control, r, p.target, bad); err == nil {
			t.Fatal("changed prior receipt admitted")
		}
	}
	if _, err := retainColdB0ReactivationPlan(ctx, p.f.observer, p.f.client, control, r, p.target, receipt, strings.Repeat("0", 64)); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("wrong approval retained", err)
	}
	for _, body := range []string{plan.body + " ", strings.Replace(plan.body, `"version":`, `"unknown":true,"version":`, 1), strings.Replace(plan.body, `"retirement_epoch":`, `"retirement_epoch":1,"retirement_epoch":`, 1)} {
		if _, err := decodeColdB0ReactivationPlan(body, coldForwardBytesDigest(body)); !errors.Is(err, ErrAuthorityLost) {
			t.Fatal("noncanonical plan admitted", err)
		}
	}
	var doc coldB0ReactivationDocument
	if json.Unmarshal([]byte(plan.body), &doc) != nil {
		t.Fatal("fixture decode failed")
	}
	doc.Forward.Request.RoutingEpoch++
	body, _ := json.Marshal(doc)
	if _, err := decodeColdB0ReactivationPlan(string(body), coldForwardBytesDigest(string(body))); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("mismatched R admitted", err)
	}
	// Retained preparation cannot be inserted outside exact immutable source context.
	err = coldTransitionTransaction(ctx, p.f.observer, func(ctx context.Context, tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `INSERT INTO crawler_ownership_b0_reactivation(plan_sha256,ordinary_restoration_plan_sha256,source_revision,retirement_epoch,target_sha256,forward_plan_sha256,payload) VALUES($1,$2,$3,$4,$5,$6,$7)`, plan.digest, r.OrdinaryRestorationPlanSHA256, r.SourceRevision, plan.document.RetirementEpoch+1, p.target.digest, plan.forward.digest, plan.body)
		return err
	})
	if err == nil {
		t.Fatal("SQL admitted mismatched retirement context")
	}
}
