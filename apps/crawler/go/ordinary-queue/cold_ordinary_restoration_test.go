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
)

func TestColdOrdinaryRestorationSchemaMatchesAppliedMigration(t *testing.T) {
	body, err := os.ReadFile("../../src/migrations/sql/crawler_ownership_ordinary_restoration.sql")
	if err != nil || string(body) != coldOrdinaryRestorationSchema {
		t.Fatal("ordinary restoration schema differs from migration")
	}
}

func ordinaryRestorationFixture(t *testing.T, native bool) (publicationFixture, ColdOrdinaryRestorationRequest) {
	t.Helper()
	ctx := context.Background()
	p := realPublicationSeedWithPrior(t, "{}", strings.Repeat("a", 40), true, native)
	rollbackFixturePosting(t, p)
	request := rollbackFixtureRequest(t, p)
	b0, err := BuildColdB0RollbackPlan(ctx, p.f.observer, p.f.client, request, p.target)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := RetainColdB0RollbackPlan(ctx, p.f.observer, p.f.client, request, p.target, b0.digest); err != nil {
		t.Fatal(err)
	}
	state, err := RestoreColdB0Rollback(ctx, p.f.observer, p.f.client, b0.digest, request.SourceRevision, p.target)
	if err != nil || state.phase != "fences-cleared" {
		t.Fatal("B0 restoration did not finish", err)
	}
	r := ColdOrdinaryRestorationRequest{ReversalSHA256: request.ReversalSHA256, SourceRevision: request.SourceRevision, RetirementEpoch: request.RetirementEpoch, B0RestorationPlanSHA256: b0.digest}
	if native {
		r.RollbackSourceRevision = strings.Repeat("b", 40)
	}
	return p, r
}

func TestRealColdOrdinaryRestorationRetainsDecisionWithoutReleasingOwnership(t *testing.T) {
	for _, native := range []bool{false, true} {
		t.Run(strconv.FormatBool(native), func(t *testing.T) {
			ctx := context.Background()
			p, r := ordinaryRestorationFixture(t, native)
			before, canonical := snapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
			plan, err := BuildColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, r, p.target)
			if err != nil {
				t.Fatal("preview failed", err)
			}
			var count int
			if err := p.f.observer.QueryRow(ctx, "SELECT count(*) FROM crawler_ownership_ordinary_restoration").Scan(&count); err != nil || count != 0 {
				t.Fatal("preview retained history", err)
			}
			for i := 0; i < 2; i++ {
				retained, err := RetainColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, r, p.target, plan.digest)
				if err != nil || retained.body != plan.body {
					t.Fatal("exact retention failed", err)
				}
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) || publicationPhase(t, p) != "reversing" {
				t.Fatal("preparation changed data or released journal")
			}
			var epoch int64
			var active int
			if err := p.f.observer.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epoch); err != nil || epoch != r.RetirementEpoch {
				t.Fatal("preparation allocated another epoch", err)
			}
			if err := p.f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_ownership_plan WHERE state='active'").Scan(&active); err != nil || active != 0 {
				t.Fatal("preparation activated an owner", err)
			}
			if native {
				if plan.Mode() != "native" || plan.fresh == nil || plan.fresh.Epoch() != r.RetirementEpoch || plan.fresh.SourceRevision() != r.RollbackSourceRevision {
					t.Fatal("fresh native decision lost rollback identity")
				}
				var state string
				if err := p.f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", plan.document.PreviousOrdinaryPlanSHA256).Scan(&state); err != nil || state != "retired" {
					t.Fatal("retired owner was reused", err)
				}
				if err := p.f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", plan.document.FreshOrdinaryPlanSHA256).Scan(&state); err != nil || state != "staged" {
					t.Fatal("fresh owner was not staged", err)
				}
			} else if plan.Mode() != "legacy" || plan.fresh != nil {
				t.Fatal("legacy decision fabricated native authority")
			}
			for _, query := range []string{"UPDATE crawler_ownership_ordinary_restoration SET created_at=created_at WHERE plan_sha256=$1", "DELETE FROM crawler_ownership_ordinary_restoration WHERE plan_sha256=$1"} {
				if _, err := p.f.observer.Exec(ctx, query, plan.digest); err == nil {
					t.Fatal("immutable history changed")
				}
			}
			// Historical inspection must work while both ownership barriers are held.
			tx, err := p.f.observer.Begin(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer tx.Rollback(ctx)
			if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(7544422533504811010),pg_advisory_xact_lock(7544422533504811009)"); err != nil {
				t.Fatal(err)
			}
			got, err := InspectColdOrdinaryRestorationPlan(ctx, p.f.observer, plan.digest, r.SourceRevision)
			if err != nil || got.body != plan.body {
				t.Fatal("inspection required ownership barriers", err)
			}
		})
	}
}

func TestRealColdOrdinaryRestorationRefusesChangedContextAndApproval(t *testing.T) {
	for _, kind := range []string{"approval", "epoch", "source", "rollback_source", "b0_history", "redis_profile", "disabled", "live_monitor", "live_posting", "tombstone"} {
		t.Run(kind, func(t *testing.T) {
			ctx := context.Background()
			p, r := ordinaryRestorationFixture(t, true)
			plan, err := BuildColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, r, p.target)
			if err != nil {
				t.Fatal(err)
			}
			approved := plan.digest
			switch kind {
			case "approval":
				approved = strings.Repeat("0", 64)
			case "epoch":
				r.RetirementEpoch++
			case "source":
				r.SourceRevision = strings.Repeat("c", 40)
			case "rollback_source":
				r.RollbackSourceRevision = strings.Repeat("c", 40)
			case "b0_history":
				r.B0RestorationPlanSHA256 = strings.Repeat("0", 64)
			case "redis_profile":
				err = p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "check_interval_minutes", "17").Err()
			case "disabled":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", p.f.task.ID)
			case "live_monitor":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET leased_until=now()+interval '1 hour' WHERE id=$1::uuid", p.f.task.ID)
			case "live_posting":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_posting SET leased_until=now()+interval '1 hour' WHERE board_id=$1::uuid", p.f.task.ID)
			case "tombstone":
				err = p.f.client.redis.Del(ctx, "lightpanda-b0:producer-owner").Err()
			}
			if err != nil {
				t.Fatal(err)
			}
			before, canonical := snapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
			if _, err := RetainColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, r, p.target, approved); err == nil {
				t.Fatal("changed approval/context retained")
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
				t.Fatal("refusal mutated data")
			}
			var count int
			if err := p.f.observer.QueryRow(ctx, "SELECT count(*) FROM crawler_ownership_ordinary_restoration").Scan(&count); err != nil || count != 0 {
				t.Fatal("refusal retained history", err)
			}
		})
	}
}

func TestRealColdOrdinaryRestorationRejectsNoncanonicalDocuments(t *testing.T) {
	p, r := ordinaryRestorationFixture(t, true)
	plan, err := BuildColdOrdinaryRestorationPlan(context.Background(), p.f.observer, p.f.client, r, p.target)
	if err != nil {
		t.Fatal(err)
	}
	for _, body := range []string{plan.body + " ", strings.Replace(plan.body, `"version":`, `"unknown":true,"version":`, 1), strings.Replace(plan.body, `"mode":"native"`, `"mode":"legacy","mode":"native"`, 1)} {
		h := sha256.Sum256([]byte(body))
		if _, err := decodeColdOrdinaryRestoration(body, hex.EncodeToString(h[:])); !errors.Is(err, ErrAuthorityLost) {
			t.Fatal("noncanonical document admitted", err)
		}
	}
	doc := plan.document
	fresh := *doc.FreshOwner
	fresh.Members = append([]ownershipMember(nil), fresh.Members...)
	fresh.Members[0].CompanyID = ordinaryID(t)
	doc.FreshOwner = &fresh
	body, _ := json.Marshal(fresh)
	h := sha256.Sum256(body)
	doc.FreshOrdinaryPlanSHA256 = hex.EncodeToString(h[:])
	body, _ = json.Marshal(doc)
	h = sha256.Sum256(body)
	if _, err := decodeColdOrdinaryRestoration(string(body), hex.EncodeToString(h[:])); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("changed rollback cohort admitted", err)
	}
}

func TestRealColdOrdinaryRestorationRetentionIsAtomicAndCannotReplaceApproval(t *testing.T) {
	ctx := context.Background()
	p, r := ordinaryRestorationFixture(t, true)
	plan, err := BuildColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, r, p.target)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_ordinary_restoration ADD CONSTRAINT private_ordinary_retention_failure CHECK(false) NOT VALID"); err != nil {
		t.Fatal(err)
	}
	if _, err := RetainColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, r, p.target, plan.digest); err == nil {
		t.Fatal("failed retention committed")
	}
	if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_ordinary_restoration DROP CONSTRAINT private_ordinary_retention_failure"); err != nil {
		t.Fatal(err)
	}
	var count int
	if err := p.f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", plan.document.FreshOrdinaryPlanSHA256).Scan(&count); err != nil || count != 0 {
		t.Fatal("failed history retained orphan stage", err)
	}
	if _, err := RetainColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, r, p.target, plan.digest); err != nil {
		t.Fatal("exact retry failed", err)
	}
	// A supported new configuration may be previewed, but cannot replace the
	// immutable approved decision or leave an unapproved fresh stage behind.
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET check_interval_minutes=17 WHERE id=$1::uuid", p.f.task.ID); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "check_interval_minutes", "17").Err(); err != nil {
		t.Fatal(err)
	}
	changed, err := BuildColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, r, p.target)
	if err != nil || changed.digest == plan.digest {
		t.Fatal("fresh changed profile not previewed", err)
	}
	if _, err := RetainColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, r, p.target, changed.digest); err == nil {
		t.Fatal("retained decision replaced")
	}
	if err := p.f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", changed.document.FreshOrdinaryPlanSHA256).Scan(&count); err != nil || count != 0 {
		t.Fatal("replacement refusal retained new stage", err)
	}
	retained, err := InspectColdOrdinaryRestorationPlan(ctx, p.f.observer, plan.digest, r.SourceRevision)
	if err != nil || retained.body != plan.body {
		t.Fatal("original history lost", err)
	}
}

func TestRealColdOrdinaryRestorationSQLRejectsChangedCohort(t *testing.T) {
	ctx := context.Background()
	p, r := ordinaryRestorationFixture(t, true)
	plan, err := BuildColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, r, p.target)
	if err != nil {
		t.Fatal(err)
	}
	doc := plan.document
	fresh := *doc.FreshOwner
	fresh.Members = append([]ownershipMember(nil), fresh.Members...)
	fresh.Members[0].CompanyID = ordinaryID(t)
	doc.FreshOwner = &fresh
	freshBody, _ := json.Marshal(fresh)
	h := sha256.Sum256(freshBody)
	doc.FreshOrdinaryPlanSHA256 = hex.EncodeToString(h[:])
	body, _ := json.Marshal(doc)
	h = sha256.Sum256(body)
	if _, err := p.f.observer.Exec(ctx, "INSERT INTO ordinary_worker_ownership_plan(plan_sha256,routing_epoch,source_revision,payload) VALUES($1,$2,$3,$4)", doc.FreshOrdinaryPlanSHA256, r.RetirementEpoch, r.RollbackSourceRevision, string(freshBody)); err != nil {
		t.Fatal("private stage unavailable", err)
	}
	_, err = p.f.observer.Exec(ctx, `INSERT INTO crawler_ownership_ordinary_restoration(plan_sha256,reversal_sha256,source_revision,retirement_epoch,b0_restoration_plan_sha256,fresh_ordinary_plan_sha256,payload) VALUES($1,$2,$3,$4,$5,$6,$7)`, hex.EncodeToString(h[:]), r.ReversalSHA256, r.SourceRevision, r.RetirementEpoch, r.B0RestorationPlanSHA256, doc.FreshOrdinaryPlanSHA256, string(body))
	if err == nil || !strings.Contains(err.Error(), "crawler_ownership_ordinary_restoration_cohort_rejected") {
		t.Fatal("SQL retained changed cohort", err)
	}
}

func TestColdOrdinaryRestorationRequestRejectsAmbiguousProtectedBytes(t *testing.T) {
	r := ColdOrdinaryRestorationRequest{ReversalSHA256: strings.Repeat("a", 64), SourceRevision: strings.Repeat("b", 40), RetirementEpoch: 147, B0RestorationPlanSHA256: strings.Repeat("c", 64), RollbackSourceRevision: strings.Repeat("d", 40)}
	body, _ := json.Marshal(r)
	h := sha256.Sum256(body)
	if got, err := DecodeColdOrdinaryRestorationRequest(string(body), hex.EncodeToString(h[:])); err != nil || got != r {
		t.Fatal("exact request refused", err)
	}
	for _, bad := range []string{string(body) + " ", strings.Replace(string(body), `"retirement_epoch":147`, `"retirement_epoch":146,"retirement_epoch":147`, 1), strings.Replace(string(body), `"source_revision":`, `"unknown":true,"source_revision":`, 1), strings.Replace(string(body), `"retirement_epoch":147`, `"retirement_epoch":1`, 1), strings.Replace(string(body), strings.Repeat("d", 40), "latest", 1)} {
		h := sha256.Sum256([]byte(bad))
		if _, err := DecodeColdOrdinaryRestorationRequest(bad, hex.EncodeToString(h[:])); !errors.Is(err, ErrAuthorityLost) {
			t.Fatal("ambiguous request admitted", err)
		}
	}
	if _, err := DecodeColdOrdinaryRestorationRequest(string(body), strings.Repeat("0", 64)); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("changed request digest admitted")
	}
}

func TestRealColdOrdinaryRestorationDowngradeRetainsHistory(t *testing.T) {
	ctx := context.Background()
	p, r := ordinaryRestorationFixture(t, true)
	plan, err := BuildColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, r, p.target)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := RetainColdOrdinaryRestorationPlan(ctx, p.f.observer, p.f.client, r, p.target, plan.digest); err != nil {
		t.Fatal(err)
	}
	uv, err := exec.LookPath("uv")
	if err != nil {
		t.Fatal("required actual Alembic downgrade unavailable")
	}
	command := exec.Command(uv, "run", "--frozen", "--no-sync", "alembic", "-c", "src/migrations/alembic.ini", "downgrade", "0044")
	command.Dir = "../.."
	command.Env = append(os.Environ(), "LOCAL_DATABASE_URL="+p.f.dsn)
	output, err := command.CombinedOutput()
	if err == nil || !strings.Contains(string(output), "crawler_ownership_history_retained") {
		t.Fatal("actual downgrade removed ordinary restoration history")
	}
	retained, err := InspectColdOrdinaryRestorationPlan(ctx, p.f.observer, plan.digest, r.SourceRevision)
	if err != nil || retained.body != plan.body {
		t.Fatal("failed downgrade lost exact decision", err)
	}
}
