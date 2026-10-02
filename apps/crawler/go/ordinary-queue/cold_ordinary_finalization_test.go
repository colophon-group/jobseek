package queue

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func TestColdOrdinaryFinalizationSchemaMatchesAppliedMigration(t *testing.T) {
	body, err := os.ReadFile("../../src/migrations/sql/crawler_ownership_restoration_finalization.sql")
	if err != nil || string(body) != coldOrdinaryFinalizationSchema {
		t.Fatal("finalization schema differs from migration", err)
	}
}
func finalizationFixture(t *testing.T, native, priorProjection bool) (publicationFixture, *ColdOrdinaryFinalizationPlan, *forwardLuaControl) {
	return finalizationFixtureWithPriorSource(t, native, priorProjection, strings.Repeat("b", 40))
}
func finalizationFixtureWithPriorSource(t *testing.T, native, priorProjection bool, priorSource string) (publicationFixture, *ColdOrdinaryFinalizationPlan, *forwardLuaControl) {
	t.Helper()
	ctx := context.Background()
	p, r, control, receipt := reactivationFixtureBootstrapWithPriorSource(t, native, "{}", strings.Repeat("a", 40), true, priorSource)
	if priorProjection {
		if err := p.f.client.redis.MSet(ctx, ownershipProjectionKey, p.plan.body, coldPublicationKey, publicationMarker(p.intent, p.spec, p.plan, "published")).Err(); err != nil {
			t.Fatal(err)
		}
	}
	b0, err := buildColdB0ReactivationPlan(ctx, p.f.observer, p.f.client, control, r, p.target, receipt)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := retainColdB0ReactivationPlan(ctx, p.f.observer, p.f.client, control, r, p.target, receipt, b0.digest); err != nil {
		t.Fatal(err)
	}
	if _, err := applyColdB0Reactivation(ctx, p.f.observer, p.f.client, control, b0.digest, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	request := ColdOrdinaryFinalizationRequest{r.OrdinaryRestorationPlanSHA256, b0.digest, p.spec.SourceRevision, ordinaryID(t), p.spec.TargetReleaseSHA256, strings.Repeat("f", 64)}
	plan, err := buildColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, request, p.target)
	if err != nil {
		t.Fatal("finalization preview rejected", err)
	}
	before := forwardRedisSnapshot(t, p.f.client)
	canonical := coldB0CanonicalSnapshot(t, p)
	if _, err := retainColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, request, p.target, strings.Repeat("f", 64)); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("wrong approval admitted", err)
	}
	for i := 0; i < 2; i++ {
		result, err := retainColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, request, p.target, plan.digest)
		if err != nil || result.body != plan.body {
			t.Fatal("exact approval retention failed", err)
		}
	}
	if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
		t.Fatal("preparation changed canonical data")
	}
	if native {
		t.Cleanup(func() {
			if _, err := p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.document.FreshOrdinaryPlanSHA256); err != nil {
				t.Error("private restored owner cleanup failed", err)
			}
		})
	}
	return p, plan, control
}
func finalizationUnselected(ctx context.Context, p publicationFixture) error {
	return pgx.BeginFunc(ctx, p.f.observer, func(tx pgx.Tx) error { return requireUnselectedAuthority(ctx, tx) })
}
func finalizationQueueOnly(t *testing.T, p publicationFixture) map[string]string {
	t.Helper()
	s := forwardRedisSnapshot(t, p.f.client)
	delete(s, ownershipProjectionKey)
	delete(s, coldPublicationKey)
	return s
}
func TestRealColdOrdinaryFinalizationPublishesAndClosesNativeAndLegacyAtR(t *testing.T) {
	for _, native := range []bool{false, true} {
		t.Run(strconv.FormatBool(native), func(t *testing.T) {
			ctx := context.Background()
			p, plan, control := finalizationFixture(t, native, false)
			canonical := coldB0CanonicalSnapshot(t, p)
			queue := finalizationQueueOnly(t, p)
			calls := control.activations
			if _, err := completeColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("unpublished approval completed", err)
			}
			if err := finalizationUnselected(ctx, p); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("legacy claims escaped reversing journal", err)
			}
			for i := 0; i < 2; i++ {
				state, err := publishColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target)
				if err != nil || state.Phase() != "published" || state.PublicationReceiptSHA256() == "" {
					t.Fatal("durable restoration publication failed", err)
				}
			}
			if publicationPhase(t, p) != "reversing" || finalizationUnselected(ctx, p) == nil {
				t.Fatal("published Redis alone released SQL ownership")
			}
			restartPublicationRedisWithoutSave(t, p.f.client)
			state, err := completeColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target)
			if err != nil || state.Phase() != "complete" || !ownershipSHA256.MatchString(state.ReceiptSHA256()) {
				t.Fatal("atomic finalization failed", err)
			}
			for i := 0; i < 2; i++ {
				again, err := completeColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target)
				if err != nil || again.ReceiptSHA256() != state.ReceiptSHA256() {
					t.Fatal("completed retry changed identity", err)
				}
			}
			reversal, err := InspectColdReversal(ctx, p.f.observer, plan.document.ReversalSHA256, p.spec.SourceRevision)
			if err != nil || reversal.Phase() != "complete" || reversal.RetirementEpoch() != plan.RetirementEpoch() || publicationPhase(t, p) != "reversed" {
				t.Fatal("original reversal/journal not closed", err)
			}
			var epoch int64
			if err := p.f.observer.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epoch); err != nil || epoch != plan.RetirementEpoch() {
				t.Fatal("finalization allocated R+1", err)
			}
			ordinary, err := InspectColdOrdinaryRestorationPlan(ctx, p.f.observer, plan.Request().OrdinaryRestorationPlanSHA256, p.spec.SourceRevision)
			if err != nil {
				t.Fatal(err)
			}
			if native {
				owner, err := OpenJointOwnedAuthority(ctx, p.f.dsn, p.f.client, epoch, ordinary.fresh.digest, ordinary.fresh.SourceRevision(), []byte(p.target.lua))
				if err != nil {
					t.Fatal("existing v1 joint reader cannot admit fresh R owner", err)
				}
				defer owner.Close()
				if err := owner.AttestOwnershipProjection(ctx, ordinary.fresh.ProjectionSHA1()); err != nil {
					t.Fatal(err)
				}
				var old string
				if err := p.f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", ordinary.document.PreviousOrdinaryPlanSHA256).Scan(&old); err != nil || old != "retired" {
					t.Fatal("retired old owner reopened", err)
				}
				if finalizationUnselected(ctx, p) == nil {
					t.Fatal("unselected legacy owner admitted with native active owner")
				}
			} else {
				if err := finalizationUnselected(ctx, p); err != nil {
					t.Fatal("legacy restoration still blocked", err)
				}
				if count := p.f.client.redis.Exists(ctx, ownershipProjectionKey).Val(); count != 0 {
					t.Fatal("legacy restoration retained native projection")
				}
			}
			if canonical != coldB0CanonicalSnapshot(t, p) || !reflect.DeepEqual(queue, finalizationQueueOnly(t, p)) || control.activations != calls {
				t.Fatal("finalization replayed B0 or canonical effects")
			}
			historical, err := InspectColdOrdinaryFinalizationApplication(ctx, p.f.observer, plan.digest, p.spec.SourceRevision)
			if err != nil || historical.digest != state.digest {
				t.Fatal("historical completed identity lost", err)
			}
		})
	}
}
func TestRealColdOrdinaryFinalizationSaveAndCommitSeamsDoNotGrantOwnership(t *testing.T) {
	for _, fault := range []string{"pending_sql", "save_denied", "published_sql", "complete_sql"} {
		t.Run(fault, func(t *testing.T) {
			ctx := context.Background()
			p, plan, control := finalizationFixture(t, true, false)
			canonical := coldB0CanonicalSnapshot(t, p)
			table, name := "", "private_finalization_failure"
			switch fault {
			case "pending_sql":
				table = "crawler_ownership_restoration_publication"
			case "published_sql":
				if err := prepareColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
					t.Fatal(err)
				}
				table = "crawler_ownership_restoration_publication"
			case "complete_sql":
				if _, err := publishColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
					t.Fatal(err)
				}
				table = "crawler_ownership_restoration_completion"
			case "save_denied":
				if err := p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "-save").Err(); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "+save").Err() })
			}
			if table != "" {
				if _, err := p.f.observer.Exec(ctx, "ALTER TABLE "+table+" ADD CONSTRAINT "+name+" CHECK(false) NOT VALID"); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					_, _ = p.f.observer.Exec(context.Background(), "ALTER TABLE "+table+" DROP CONSTRAINT IF EXISTS "+name)
				})
			}
			var err error
			if fault == "complete_sql" {
				_, err = completeColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target)
			} else {
				_, err = publishColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target)
			}
			if err == nil {
				t.Fatal("failed publication/commit admitted")
			}
			state, err := InspectColdOrdinaryFinalizationApplication(ctx, p.f.observer, plan.digest, p.spec.SourceRevision)
			if err != nil || state.Phase() == "complete" {
				t.Fatal("failed SQL claimed completion", err)
			}
			if publicationPhase(t, p) != "reversing" || finalizationUnselected(ctx, p) == nil {
				t.Fatal("failed SQL released ownership")
			}
			if table != "" {
				if _, err := p.f.observer.Exec(ctx, "ALTER TABLE "+table+" DROP CONSTRAINT "+name); err != nil {
					t.Fatal(err)
				}
			}
			if fault == "save_denied" {
				if err := p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "+save").Err(); err != nil {
					t.Fatal(err)
				}
			}
			if fault == "published_sql" || fault == "complete_sql" {
				restartPublicationRedisWithoutSave(t, p.f.client)
			}
			if _, err := publishColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
				t.Fatal("exact publication recovery failed", err)
			}
			if _, err := completeColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
				t.Fatal("exact finalization recovery failed", err)
			}
			if canonical != coldB0CanonicalSnapshot(t, p) {
				t.Fatal("recovery changed canonical data")
			}
		})
	}
}
func TestRealColdOrdinaryFinalizationRejectsLostEvidenceAndDrift(t *testing.T) {
	for _, fault := range []string{"lost_pending", "lost_published", "changed_marker", "ordinary_config", "live_lease", "canonical_due", "epoch", "producer_owner"} {
		t.Run(fault, func(t *testing.T) {
			ctx := context.Background()
			p, plan, control := finalizationFixture(t, true, false)
			if fault == "lost_published" {
				if _, err := publishColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
					t.Fatal(err)
				}
			} else if fault == "lost_pending" {
				if err := prepareColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
					t.Fatal(err)
				}
			}
			switch fault {
			case "lost_pending", "lost_published":
				if err := p.f.client.redis.Del(ctx, coldPublicationKey).Err(); err != nil {
					t.Fatal(err)
				}
			case "changed_marker":
				if err := p.f.client.redis.Set(ctx, coldPublicationKey, "foreign", 0).Err(); err != nil {
					t.Fatal(err)
				}
			case "ordinary_config":
				if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET metadata='{\"monitor_type\":\"greenhouse\",\"monitor_config\":{\"token\":\"changed\"}}'::jsonb WHERE id=$1::uuid", p.f.task.ID); err != nil {
					t.Fatal(err)
				}
			case "live_lease":
				if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET leased_until=now()+interval '1 minute' WHERE id=$1::uuid", p.f.task.ID); err != nil {
					t.Fatal(err)
				}
			case "canonical_due":
				if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=next_scrape_at+interval '1 second' WHERE board_id=$1::uuid", p.target.document.Boards[0].ID); err != nil {
					t.Fatal(err)
				}
			case "epoch":
				if _, err := p.f.observer.Exec(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')"); err != nil {
					t.Fatal(err)
				}
			case "producer_owner":
				if err := p.f.client.redis.HSet(ctx, "lightpanda-b0:producer-owner", "cohort", "cdom").Err(); err != nil {
					t.Fatal(err)
				}
			}
			before := forwardRedisSnapshot(t, p.f.client)
			if _, err := publishColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err == nil {
				t.Fatal("drift/lost witness admitted")
			}
			if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) || publicationPhase(t, p) != "reversing" {
				t.Fatal("drift silently repaired")
			}
		})
	}
}
func TestRealColdOrdinaryFinalizationDirectSQLCannotCommitPartialAuthority(t *testing.T) {
	ctx := context.Background()
	p, plan, control := finalizationFixture(t, true, false)
	if _, err := publishColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	for _, query := range []string{
		"UPDATE crawler_ownership_reversal SET phase='complete' WHERE reversal_sha256='" + plan.document.ReversalSHA256 + "'",
		"UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256='" + plan.document.FreshOrdinaryPlanSHA256 + "'",
	} {
		if _, err := p.f.observer.Exec(ctx, query); err == nil {
			t.Fatal("partial authority committed without completion")
		}
	}
	if err := coldTransitionTransaction(ctx, p.f.observer, func(ctx context.Context, tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "UPDATE crawler_ownership_reversal SET phase='complete' WHERE reversal_sha256=$1", plan.document.ReversalSHA256); err != nil {
			return err
		}
		_, err := tx.Exec(ctx, "UPDATE crawler_ownership_transition SET phase='reversed' WHERE intent_sha256=$1", p.intent)
		return err
	}); err == nil {
		t.Fatal("reversal and old journal closed without final receipt")
	}
	if publicationPhase(t, p) != "reversing" {
		t.Fatal("failed commit leaked reversed journal")
	}
	if _, err := completeColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
}
func TestRealColdOrdinaryLegacyPublicationContainsPartialLuaCommandFailure(t *testing.T) {
	ctx := context.Background()
	p, plan, control := finalizationFixture(t, false, true)
	if err := prepareColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "-del").Err(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "+del").Err() })
	if _, err := publishColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err == nil {
		t.Fatal("partial legacy Lua failure admitted")
	}
	if err := p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "+del").Err(); err != nil {
		t.Fatal(err)
	}
	before := forwardRedisSnapshot(t, p.f.client)
	if _, err := publishColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err == nil {
		t.Fatal("partial legacy publication repaired")
	}
	if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) || publicationPhase(t, p) != "reversing" || finalizationUnselected(ctx, p) == nil {
		t.Fatal("partial publication released ownership")
	}
}
func TestRealColdOrdinaryFinalizationRejectsCanonicalDecoderDrift(t *testing.T) {
	p, plan, _ := finalizationFixture(t, true, false)
	for _, body := range []string{plan.body + "\n", strings.Replace(plan.body, `"version":`, `"unknown":"secret","version":`, 1), strings.Replace(plan.body, `"retirement_epoch":`, `"retirement_epoch":0,"retirement_epoch":`, 1)} {
		if _, err := decodeColdOrdinaryFinalization(body, coldForwardBytesDigest(body)); !errors.Is(err, ErrAuthorityLost) {
			t.Fatal("rehashed ambiguous approval admitted", err)
		}
	}
	changed := plan.document
	changed.RetirementEpoch = 1
	body, _ := json.Marshal(changed)
	if _, err := decodeColdOrdinaryFinalization(string(body), coldForwardBytesDigest(string(body))); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal(err)
	}
	for _, table := range []string{"crawler_ownership_restoration_finalization", "crawler_ownership_restoration_publication", "crawler_ownership_restoration_completion"} {
		if _, err := p.f.observer.Exec(context.Background(), "DELETE FROM "+table+" WHERE plan_sha256=$1", plan.digest); table == "crawler_ownership_restoration_finalization" && err == nil {
			t.Fatal("retained approval discarded")
		}
	}
}

func TestRealColdOrdinaryFinalizationRequiresCompletedB0(t *testing.T) {
	ctx := context.Background()
	p, b0, control := retainedReactivation(t, true)
	r := ColdOrdinaryFinalizationRequest{b0.Request().OrdinaryRestorationPlanSHA256, b0.digest, p.spec.SourceRevision, ordinaryID(t), p.spec.TargetReleaseSHA256, strings.Repeat("f", 64)}
	before := forwardRedisSnapshot(t, p.f.client)
	calls := control.activations
	if _, err := buildColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, r, p.target); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("prepared B0 intent granted finalization", err)
	}
	if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) || control.activations != calls {
		t.Fatal("finalization preview activated B0")
	}
}

func TestRealColdOrdinaryFinalizationRetainsHistoryAndInspectsWithoutAuthority(t *testing.T) {
	ctx := context.Background()
	p, plan, control := finalizationFixture(t, true, false)
	if _, err := publishColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	state, err := completeColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target)
	if err != nil {
		t.Fatal(err)
	}
	for _, table := range []string{"crawler_ownership_restoration_finalization", "crawler_ownership_restoration_publication", "crawler_ownership_restoration_completion"} {
		for _, query := range []string{"UPDATE " + table + " SET created_at=created_at WHERE plan_sha256=$1", "DELETE FROM " + table + " WHERE plan_sha256=$1"} {
			if _, err := p.f.observer.Exec(ctx, query, plan.digest); err == nil {
				t.Fatal("immutable finalization history changed")
			}
		}
	}
	uv, err := exec.LookPath("uv")
	if err != nil {
		t.Fatal("required actual Alembic unavailable")
	}
	command := exec.Command(uv, "run", "--frozen", "--no-sync", "alembic", "-c", "src/migrations/alembic.ini", "downgrade", "0046")
	command.Dir = "../.."
	command.Env = append(os.Environ(), "LOCAL_DATABASE_URL="+p.f.dsn)
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "crawler_ownership_history_retained") {
		t.Fatal("actual downgrade discarded finalization history")
	}
	if _, err := p.f.observer.Exec(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')"); err != nil {
		t.Fatal(err)
	}
	before := forwardRedisSnapshot(t, p.f.client)
	if _, err := completeColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err == nil {
		t.Fatal("completion retry adopted advanced allocator")
	}
	if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) {
		t.Fatal("refused completion changed Redis")
	}
	tx, err := p.f.observer.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1),pg_advisory_xact_lock($2)", OrdinaryLeaseBarrier, routingEpochBarrier); err != nil {
		t.Fatal(err)
	}
	inspectCtx, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	inspected, err := InspectColdOrdinaryFinalizationApplication(inspectCtx, p.f.observer, plan.digest, p.spec.SourceRevision)
	if err != nil || inspected.body != state.body || inspected.plan.body != plan.body {
		t.Fatal("historical inspection required current authority or barriers", err)
	}
}

func TestColdOrdinaryFinalizationProtectedRequestRejectsAmbiguousBytes(t *testing.T) {
	r := ColdOrdinaryFinalizationRequest{strings.Repeat("1", 64), strings.Repeat("2", 64), strings.Repeat("a", 40), "00000000-0000-4000-8000-000000000001", strings.Repeat("3", 64), strings.Repeat("4", 64)}
	body, _ := json.Marshal(r)
	if decoded, err := DecodeColdOrdinaryFinalizationRequest(string(body), coldForwardBytesDigest(string(body))); err != nil || decoded != r {
		t.Fatal("canonical protected request refused", err)
	}
	for _, bad := range []string{string(body) + "\n", strings.Replace(string(body), `"source_revision":`, `"unknown":"secret","source_revision":`, 1), strings.Replace(string(body), `"source_revision":`, `"source_revision":"invalid","source_revision":`, 1), strings.Replace(string(body), r.TransitionID, "latest", 1), strings.Replace(string(body), r.ActiveReleaseSHA256, "", 1), strings.Repeat("x", 4097)} {
		if _, err := DecodeColdOrdinaryFinalizationRequest(bad, coldForwardBytesDigest(bad)); !errors.Is(err, ErrAuthorityLost) || strings.Contains(err.Error(), "secret") {
			t.Fatal("rehashed ambiguous request admitted or leaked")
		}
	}
	if _, err := DecodeColdOrdinaryFinalizationRequest(string(body), strings.Repeat("f", 64)); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("wrong request digest admitted")
	}
}
