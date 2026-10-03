//go:build integration && linux

package queue

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	"github.com/jackc/pgx/v5"
)

// Runs only inside the existing disposable root/UID-10001 producer fixture.
// Prior release/receipt labels remain synthetic; an actual old immutable worker
// needs separate independent source/image capability verification.
func installedColdOrdinaryFinalizationCLI(t *testing.T, p publicationFixture, ordinary *ColdOrdinaryRestorationPlan, b0 *ColdB0ReactivationPlan, control *b0producer.Client, source string, env map[string]string, command func(string) *exec.Cmd, write func(string, string) string, stop, start func()) {
	t.Helper()
	ctx := context.Background()
	r := ColdOrdinaryFinalizationRequest{ordinary.digest, b0.digest, source, ordinaryID(t), p.spec.TargetReleaseSHA256, strings.Repeat("f", 64)}
	plan, err := BuildColdOrdinaryFinalizationPlan(ctx, p.f.observer, p.f.client, control, r, p.target)
	if err != nil {
		t.Fatal("actual producer finalization preview rejected", err)
	}
	requestBody, _ := json.Marshal(r)
	env["ORDINARY_COLD_FINALIZATION_REQUEST_FILE"], env["ORDINARY_COLD_FINALIZATION_REQUEST_SHA256"] = write("finalization.json", string(requestBody)), coldForwardBytesDigest(string(requestBody))
	type identity struct {
		Operation          string          `json:"operation"`
		Source             string          `json:"source_revision"`
		PlanSHA            string          `json:"ordinary_finalization_plan_sha256"`
		Plan               json.RawMessage `json:"ordinary_finalization_plan"`
		Phase              string          `json:"ordinary_finalization_phase"`
		PublicationSHA     string          `json:"ordinary_publication_receipt_sha256"`
		ReceiptSHA         string          `json:"ordinary_finalization_receipt_sha256"`
		Receipt            json.RawMessage `json:"ordinary_finalization_receipt"`
		Retirement         int64           `json:"retirement_routing_epoch"`
		RestoredSHA        string          `json:"restored_ordinary_plan_sha256"`
		RestoredSource     string          `json:"restored_ordinary_source_revision"`
		RestoredProjection string          `json:"restored_ordinary_projection_sha1"`
	}
	call := func(operation string, accepted bool) identity {
		t.Helper()
		output, err := command(operation).CombinedOutput()
		if !accepted {
			if err == nil || strings.Contains(string(output), p.f.dsn) || !strings.Contains(string(output), "ordinary cold coordinator") {
				t.Fatal("finalization CLI admitted drift or exposed input", operation)
			}
			return identity{}
		}
		var result identity
		if err != nil || json.Unmarshal(output, &result) != nil || result.Operation != operation || result.Source != source || result.PlanSHA != plan.digest || string(result.Plan) != plan.body || result.Retirement != plan.RetirementEpoch() {
			t.Fatal("finalization CLI lost exact retained identity", operation)
		}
		if ordinary.fresh != nil && (result.RestoredSHA != ordinary.fresh.digest || result.RestoredSource != ordinary.fresh.SourceRevision() || result.RestoredProjection != ordinary.fresh.ProjectionSHA1()) {
			t.Fatal("CLI lost restored ordinary identity")
		}
		if ordinary.fresh == nil && (result.RestoredSHA != "" || result.RestoredSource != "" || result.RestoredProjection != "") {
			t.Fatal("CLI invented legacy native owner")
		}
		return result
	}
	before, canonical := forwardRedisSnapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
	call("cold-ordinary-finalization-plan", true)
	originalHash := env["ORDINARY_COLD_FINALIZATION_REQUEST_SHA256"]
	env["ORDINARY_COLD_FINALIZATION_REQUEST_SHA256"] = strings.Repeat("f", 64)
	call("cold-ordinary-finalization-plan", false)
	env["ORDINARY_COLD_FINALIZATION_REQUEST_SHA256"] = originalHash
	env["ORDINARY_COLD_FINALIZATION_PLAN_SHA256"] = strings.Repeat("f", 64)
	call("cold-ordinary-finalization-retain", false)
	env["ORDINARY_COLD_FINALIZATION_PLAN_SHA256"] = plan.digest
	call("cold-ordinary-finalization-retain", true)
	call("cold-ordinary-finalization-retain", true)
	call("cold-ordinary-finalization-complete", false)
	if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
		t.Fatal("finalizer preview/retention changed canonical queues")
	}
	for key, value := range map[string]string{"ORDINARY_COLD_ROUTING_EPOCH": strconv.FormatInt(p.plan.Epoch()+1, 10), "ORDINARY_COLD_RETIREMENT_EPOCH": strconv.FormatInt(plan.RetirementEpoch()+1, 10), "ORDINARY_COLD_PLAN_SHA256": strings.Repeat("f", 64)} {
		old := env[key]
		env[key] = value
		call("cold-ordinary-finalization-prepare", false)
		env[key] = old
	}
	if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) {
		t.Fatal("refused finalizer input changed Redis")
	}
	if ordinary.fresh != nil {
		t.Cleanup(func() {
			_, err := p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", ordinary.fresh.digest)
			if err != nil {
				t.Error("private restored owner cleanup failed", err)
			}
		})
	}
	call("cold-ordinary-finalization-prepare", true)
	call("cold-ordinary-finalization-prepare", true)
	if publicationPhase(t, p) != "reversing" || finalizationUnselected(ctx, p) == nil {
		t.Fatal("pending finalizer admitted claims")
	}
	// Real SQL triggers pause the real CLI after effects. There are no runtime
	// test hooks. SIGKILL before COMMIT must leave every SQL authority contained.
	killAt := func(operation, table, event, queryPattern string, lockID int32) {
		t.Helper()
		name := "finalizer_crash_" + strings.ReplaceAll(p.f.company, "-", "") + "_" + strconv.FormatInt(int64(lockID), 10)
		quoted := pgx.Identifier{name}.Sanitize()
		if _, err := p.f.observer.Exec(ctx, "CREATE FUNCTION "+quoted+"() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(18273645,"+strconv.FormatInt(int64(lockID), 10)+"); RETURN NEW; END $$"); err != nil {
			t.Fatal(err)
		}
		if _, err := p.f.observer.Exec(ctx, "CREATE TRIGGER "+quoted+" AFTER "+event+" ON "+table+" FOR EACH ROW EXECUTE FUNCTION "+quoted+"()"); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() {
			_, _ = p.f.observer.Exec(context.Background(), "DROP TRIGGER IF EXISTS "+quoted+" ON "+table)
			_, _ = p.f.observer.Exec(context.Background(), "DROP FUNCTION "+quoted+"()")
		})
		lock, err := p.f.observer.Acquire(ctx)
		if err != nil {
			t.Fatal(err)
		}
		defer lock.Release()
		if _, err := lock.Exec(ctx, "SELECT pg_advisory_lock(18273645,$1)", lockID); err != nil {
			t.Fatal(err)
		}
		defer lock.Exec(ctx, "SELECT pg_advisory_unlock(18273645,$1)", lockID)
		cmd := command(operation)
		if err := cmd.Start(); err != nil {
			t.Fatal("finalizer CLI failed to start")
		}
		done := make(chan error, 1)
		go func() { done <- cmd.Wait() }()
		waited := false
		t.Cleanup(func() {
			if !waited {
				_ = cmd.Process.Kill()
				<-done
			}
		})
		deadline := time.Now().Add(6 * time.Second)
		for {
			var paused bool
			if err := p.f.observer.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='jobseek:crawler:ordinary-cold-coordinator:local' AND wait_event_type='Lock' AND query LIKE $1)", queryPattern).Scan(&paused); err != nil {
				t.Fatal(err)
			}
			if paused {
				break
			}
			if time.Now().After(deadline) {
				t.Fatal("actual finalizer CLI did not reach SQL crash seam", operation)
			}
			time.Sleep(10 * time.Millisecond)
		}
		var active bool
		if err := p.f.observer.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ordinary_worker_ownership_plan WHERE state='active')").Scan(&active); err != nil || active || publicationPhase(t, p) != "reversing" {
			t.Fatal("uncommitted finalizer admitted SQL authority", err)
		}
		if err := cmd.Process.Kill(); err != nil {
			t.Fatal("actual finalizer SIGKILL failed")
		}
		<-done
		waited = true
		if _, err := lock.Exec(ctx, "SELECT pg_advisory_unlock(18273645,$1)", lockID); err != nil {
			t.Fatal(err)
		}
		if _, err := p.f.observer.Exec(ctx, "DROP TRIGGER "+quoted+" ON "+table); err != nil {
			t.Fatal(err)
		}
	}
	killAt("cold-ordinary-finalization-publish", "crawler_ownership_restoration_publication", "UPDATE", "%UPDATE crawler_ownership_restoration_publication%", 915281)
	state, err := InspectColdOrdinaryFinalizationApplication(ctx, p.f.observer, plan.digest, source)
	if err != nil || state.Phase() != "publishing" {
		t.Fatal("post-SAVE SIGKILL retained false publication receipt", err)
	}
	saved := forwardRedisSnapshot(t, p.f.client)
	stop()
	restartPublicationRedisWithoutSave(t, p.f.client)
	if err := os.Chown(p.f.client.redis.Options().Addr, 10001, 10001); err != nil {
		t.Fatal(err)
	}
	assertFinalizationReloadSnapshot(t, saved, forwardRedisSnapshot(t, p.f.client), "RDB reload before producer startup")
	start()
	assertFinalizationReloadSnapshot(t, saved, forwardRedisSnapshot(t, p.f.client), "producer startup after RDB reload")
	published := call("cold-ordinary-finalization-publish", true)
	if published.Phase != "published" || !ownershipSHA256.MatchString(published.PublicationSHA) || published.ReceiptSHA != "" {
		t.Fatal("CLI did not durably publish ordinary restoration")
	}
	killAt("cold-ordinary-finalization-complete", "crawler_ownership_restoration_completion", "INSERT", "%INSERT INTO crawler_ownership_restoration_completion%", 915282)
	state, err = InspectColdOrdinaryFinalizationApplication(ctx, p.f.observer, plan.digest, source)
	if err != nil || state.Phase() != "published" || publicationPhase(t, p) != "reversing" {
		t.Fatal("completion SIGKILL leaked partial SQL authority", err)
	}
	finished := call("cold-ordinary-finalization-complete", true)
	if finished.Phase != "complete" || !ownershipSHA256.MatchString(finished.ReceiptSHA) || coldForwardBytesDigest(string(finished.Receipt)) != finished.ReceiptSHA || finished.PublicationSHA != published.PublicationSHA {
		t.Fatal("CLI did not atomically complete restoration")
	}
	if again := call("cold-ordinary-finalization-complete", true); again.ReceiptSHA != finished.ReceiptSHA || string(again.Receipt) != string(finished.Receipt) {
		t.Fatal("completed CLI finalizer retry changed receipt")
	}
	if publicationPhase(t, p) != "reversed" {
		t.Fatal("CLI left original joint journal open")
	}
	if ordinary.fresh == nil {
		if err := finalizationUnselected(ctx, p); err != nil {
			t.Fatal("legacy claims still blocked after finalization", err)
		}
	} else {
		owner, err := OpenJointOwnedAuthority(ctx, p.f.dsn, p.f.client, plan.RetirementEpoch(), ordinary.fresh.digest, ordinary.fresh.SourceRevision(), []byte(p.target.lua))
		if err != nil {
			t.Fatal("existing v1 reader refused restored owner", err)
		}
		defer owner.Close()
		if err := owner.AttestOwnershipProjection(ctx, ordinary.fresh.ProjectionSHA1()); err != nil {
			t.Fatal(err)
		}
		if finalizationUnselected(ctx, p) == nil {
			t.Fatal("legacy unselected claims escaped native restored owner")
		}
	}
	if canonical != coldB0CanonicalSnapshot(t, p) || !reflect.DeepEqual(saved, forwardRedisSnapshot(t, p.f.client)) {
		t.Fatal("finalizer changed canonical rows or saved queues")
	}
	stop()
	for _, key := range []string{"REDIS_URL", "ORDINARY_COLD_B0_TARGET_FILE", "ORDINARY_COLD_B0_TARGET_SHA256", "ORDINARY_COLD_B0_LUA_FILE"} {
		delete(env, key)
	}
	lock, err := p.f.observer.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Release()
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_lock($1),pg_advisory_lock($2)", OrdinaryLeaseBarrier, routingEpochBarrier); err != nil {
		t.Fatal(err)
	}
	defer lock.Exec(ctx, "SELECT pg_advisory_unlock($1),pg_advisory_unlock($2)", OrdinaryLeaseBarrier, routingEpochBarrier)
	observed := call("cold-ordinary-finalization-inspect", true)
	if observed.ReceiptSHA != finished.ReceiptSHA || observed.Phase != "complete" || string(observed.Receipt) != string(finished.Receipt) {
		t.Fatal("historical CLI inspection lost finalization")
	}
}

// Report only key names and digests when the private reload proof differs. This
// distinguishes persistence loss from producer startup effects without exposing
// connection inputs or relaxing exact full-state conservation.
func assertFinalizationReloadSnapshot(t *testing.T, before, after map[string]string, stage string) {
	t.Helper()
	if reflect.DeepEqual(before, after) {
		return
	}
	keys := map[string]bool{}
	for key := range before {
		keys[key] = true
	}
	for key := range after {
		keys[key] = true
	}
	var changed []string
	for key := range keys {
		old, existed := before[key]
		current, exists := after[key]
		if existed != exists || old != current {
			changed = append(changed, key)
		}
	}
	sort.Strings(changed)
	for _, key := range changed {
		_, existed := before[key]
		_, exists := after[key]
		t.Logf("reload drift key=%q before_present=%t before_sha256=%s after_present=%t after_sha256=%s", key, existed, coldForwardBytesDigest(before[key]), exists, coldForwardBytesDigest(after[key]))
	}
	t.Fatal("saved ordinary publication differs at", stage)
}
