package queue

import (
	"context"
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestColdB0RestorationSchemaMatchesAppliedMigration(t *testing.T) {
	body, err := os.ReadFile("../../src/migrations/sql/crawler_ownership_b0_restoration.sql")
	if err != nil || string(body) != coldB0RestorationSchema {
		t.Fatal("retained restoration schema differs from migration")
	}
}

func retainedB0Restoration(t *testing.T, kind string) (publicationFixture, *ColdB0RollbackPlan, string) {
	t.Helper()
	p := realPublication(t)
	id := rollbackFixturePosting(t, p)
	rollbackFixtureState(t, p, kind)
	request := rollbackFixtureRequest(t, p)
	plan, err := BuildColdB0RollbackPlan(context.Background(), p.f.observer, p.f.client, request, p.target)
	if err != nil {
		t.Fatal(err)
	}
	before, canonical := snapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
	state, err := RetainColdB0RollbackPlan(context.Background(), p.f.observer, p.f.client, request, p.target, plan.digest)
	if err != nil || state.phase != "prepared" || state.plan.body != plan.body {
		t.Fatal("exact plan not retained before restoration", err)
	}
	if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
		t.Fatal("retention mutated Redis/canonical data")
	}
	if _, err := RetainColdB0RollbackPlan(context.Background(), p.f.observer, p.f.client, request, p.target, plan.digest); err != nil {
		t.Fatal("exact preparation retry failed", err)
	}
	return p, plan, id
}

func TestRealColdB0RestorationDurableRetryPreservesCanonicalAndDue(t *testing.T) {
	for _, kind := range []string{"ready", "terminal", "dead"} {
		t.Run(kind, func(t *testing.T) {
			p, plan, id := retainedB0Restoration(t, kind)
			ctx := context.Background()
			canonical := coldB0CanonicalSnapshot(t, p)
			if err := p.f.client.redis.Set(ctx, "unrelated:restoration-proof", "untouched", 0).Err(); err != nil {
				t.Fatal(err)
			}
			state, err := RestoreColdB0Rollback(ctx, p.f.observer, p.f.client, plan.digest, plan.document.Request.SourceRevision, p.target)
			if err != nil || state.phase != "fences-cleared" {
				t.Fatal("native B0 restoration failed", err)
			}
			if publicationPhase(t, p) != "reversing" || canonical != coldB0CanonicalSnapshot(t, p) {
				t.Fatal("restoration released joint authority/replayed canonical data")
			}
			var fences int
			if err := p.f.observer.QueryRow(ctx, "SELECT count(*) FROM lightpanda_b0_write_fence WHERE job_posting_id=$1::uuid", id).Scan(&fences); err != nil || fences != 0 {
				t.Fatal("exact historical fence not cleared")
			}
			before := snapshot(t, p.f.client)
			if value := p.f.client.redis.Get(ctx, "unrelated:restoration-proof").Val(); value != "untouched" {
				t.Fatal("unrelated Redis state changed")
			}
			if retry, err := RestoreColdB0Rollback(ctx, p.f.observer, p.f.client, plan.digest, plan.document.Request.SourceRevision, p.target); err != nil || retry.phase != "fences-cleared" {
				t.Fatal("exact restoration retry failed", err)
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
				t.Fatal("retry replayed data/queue effects")
			}
			// No-save stop and independent process load prove the acknowledged
			// RDB has the actual tombstone/restored schedule/config, not memory only.
			restartPublicationRedisWithoutSave(t, p.f.client)
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
				t.Fatal("restoration SAVE lost exact queue/tombstone state")
			}
			if retry, err := RestoreColdB0Rollback(ctx, p.f.observer, p.f.client, plan.digest, plan.document.Request.SourceRevision, p.target); err != nil || retry.phase != "fences-cleared" {
				t.Fatal("RDB recovery failed", err)
			}
			if _, err := p.f.observer.Exec(ctx, "UPDATE crawler_ownership_transition SET phase='reversed' WHERE intent_sha256=$1", p.intent); err == nil {
				t.Fatal("B0 restoration alone released ordinary/host readiness")
			}
		})
	}
}

func TestRealColdB0RestorationRecoversRedisSaveBeforeSQLCommit(t *testing.T) {
	for _, seam := range []string{"redis_progress", "fence_progress", "save_denied"} {
		t.Run(seam, func(t *testing.T) {
			p, plan, id := retainedB0Restoration(t, "terminal")
			ctx := context.Background()
			canonical := coldB0CanonicalSnapshot(t, p)
			phase := "redis-restored"
			if seam == "fence_progress" {
				phase = "fences-cleared"
			}
			if seam == "save_denied" {
				if err := p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "-save").Err(); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = p.f.client.redis.Do(context.Background(), "ACL", "SETUSER", "default", "+save").Err() })
			} else {
				if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_b0_restoration ADD CONSTRAINT private_restore_crash CHECK(phase <> '"+phase+"')"); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					_, _ = p.f.observer.Exec(context.Background(), "ALTER TABLE crawler_ownership_b0_restoration DROP CONSTRAINT IF EXISTS private_restore_crash")
				})
			}
			if _, err := RestoreColdB0Rollback(ctx, p.f.observer, p.f.client, plan.digest, plan.document.Request.SourceRevision, p.target); err == nil {
				t.Fatal("failed seam admitted progress")
			}
			retained, err := InspectColdB0Restoration(ctx, p.f.observer, plan.digest, plan.document.Request.SourceRevision)
			expected := "prepared"
			if seam == "fence_progress" {
				expected = "redis-restored"
			}
			if err != nil || retained.phase != expected {
				t.Fatal("durable seam identity/phase lost", err)
			}
			var fences int
			if err := p.f.observer.QueryRow(ctx, "SELECT count(*) FROM lightpanda_b0_write_fence WHERE job_posting_id=$1::uuid", id).Scan(&fences); err != nil || fences != 1 {
				t.Fatal("uncommitted historical cleanup escaped SQL rollback")
			}
			if canonical != coldB0CanonicalSnapshot(t, p) || publicationPhase(t, p) != "reversing" {
				t.Fatal("failed seam replayed canonical data/released claims")
			}
			if witness, err := coldB0RestoreWitness(ctx, p.f.client, p.target, plan); err != nil || witness != "restored" {
				t.Fatal("uncertain Redis commit not inspectable", err)
			}
			if seam == "save_denied" {
				if err := p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "+save").Err(); err != nil {
					t.Fatal(err)
				}
			} else if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_b0_restoration DROP CONSTRAINT private_restore_crash"); err != nil {
				t.Fatal(err)
			}
			before := snapshot(t, p.f.client)
			if state, err := RestoreColdB0Rollback(ctx, p.f.observer, p.f.client, plan.digest, plan.document.Request.SourceRevision, p.target); err != nil || state.phase != "fences-cleared" {
				t.Fatal("exact tombstone recovery failed", err)
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
				t.Fatal("seam recovery replayed queue/canonical effects")
			}
		})
	}
}

func TestRealColdB0RestorationRejectsChangedApprovalAndLostTombstones(t *testing.T) {
	for _, fault := range []string{"canonical_before_apply", "queue_before_apply", "wrong_target", "wrong_source", "advanced_epoch", "missing_tombstone", "expired_tombstone", "wrong_receipt", "unexpected_namespace"} {
		t.Run(fault, func(t *testing.T) {
			p, plan, id := retainedB0Restoration(t, "terminal")
			ctx := context.Background()
			target := p.target
			revision := plan.document.Request.SourceRevision
			if strings.Contains(fault, "tombstone") || fault == "wrong_receipt" || fault == "unexpected_namespace" {
				if _, err := RestoreColdB0Rollback(ctx, p.f.observer, p.f.client, plan.digest, revision, target); err != nil {
					t.Fatal(err)
				}
			}
			var err error
			switch fault {
			case "canonical_before_apply":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=next_scrape_at+interval '1 hour' WHERE id=$1::uuid", id)
			case "queue_before_apply":
				err = p.f.client.redis.HDel(ctx, "lightpanda-b0:legacy-guard", id).Err()
			case "wrong_target":
				copy := *p.target
				copy.digest = strings.Repeat("9", 64)
				target = &copy
			case "wrong_source":
				revision = strings.Repeat("b", 40)
			case "advanced_epoch":
				_, err = p.f.observer.Exec(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')")
			case "missing_tombstone":
				err = p.f.client.redis.Del(ctx, "lightpanda-b0:producer-owner").Err()
			case "expired_tombstone":
				err = p.f.client.redis.PExpire(ctx, "lightpanda-b0:producer-owner", time.Hour).Err()
			case "wrong_receipt":
				err = p.f.client.redis.HSet(ctx, "lightpanda-b0:producer-owner", "source_receipt_sha256", strings.Repeat("9", 64)).Err()
			case "unexpected_namespace":
				err = p.f.client.redis.HSet(ctx, p.target.keys()[0], "unknown", "secret").Err()
			}
			if err != nil {
				t.Fatal(err)
			}
			before, canonical := snapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
			if _, err := RestoreColdB0Rollback(ctx, p.f.observer, p.f.client, plan.digest, revision, target); err == nil || strings.Contains(err.Error(), "secret") {
				t.Fatal("stale/lost restoration evidence admitted or exposed")
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
				t.Fatal("rejected restoration changed queue/canonical data")
			}
			if retained, err := InspectColdB0Restoration(ctx, p.f.observer, plan.digest, plan.document.Request.SourceRevision); err != nil || retained.plan.body != plan.body {
				t.Fatal("retained inspection lost identity after fault", err)
			}
		})
	}
}

func TestRealColdB0RestorationHistoryCannotBeRewrittenOrSkipped(t *testing.T) {
	p, plan, _ := retainedB0Restoration(t, "terminal")
	ctx := context.Background()
	for _, query := range []string{"DELETE FROM crawler_ownership_b0_restoration WHERE plan_sha256=$1", "UPDATE crawler_ownership_b0_restoration SET payload=payload||' ' WHERE plan_sha256=$1", "UPDATE crawler_ownership_b0_restoration SET retirement_epoch=retirement_epoch+1 WHERE plan_sha256=$1", "UPDATE crawler_ownership_b0_restoration SET phase='fences-cleared' WHERE plan_sha256=$1"} {
		if _, err := p.f.observer.Exec(ctx, query, plan.digest); err == nil {
			t.Fatal("approved history rewritten/skipped")
		}
	}
	before := snapshot(t, p.f.client)
	if _, err := RetainColdB0RollbackPlan(ctx, p.f.observer, p.f.client, plan.document.Request, p.target, strings.Repeat("9", 64)); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("wrong plan approval accepted")
	}
	if !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
		t.Fatal("wrong approval mutated Redis")
	}
}
