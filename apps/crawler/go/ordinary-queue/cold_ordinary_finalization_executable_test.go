package queue

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestRealColdOrdinaryFinalizationExecutableInspectsExactHistoryWithoutLiveInputs(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "ordinary-go-worker")
	fixtureSource := strings.Repeat("a", 40)
	// Build once before opening either fixture. Local source labels are synthetic;
	// Linux release fixtures bind identity to the independently checked Git source.
	buildContext, cancelBuild := context.WithTimeout(context.Background(), time.Minute)
	defer cancelBuild()
	build := exec.CommandContext(buildContext, "go", "build", "-buildvcs=false", "-trimpath", "-ldflags=-X main.sourceRevision="+fixtureSource, "-o", binary, "./cmd/live")
	build.Dir = "../ordinary-worker"
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatal("actual finalizer executable build failed", err, string(output))
	}
	for _, native := range []bool{false, true} {
		t.Run(strconv.FormatBool(native), func(t *testing.T) {
			ctx := context.Background()
			p, plan, control := finalizationFixture(t, native, false)
			if p.spec.SourceRevision != fixtureSource {
				t.Fatal("actual executable source differs from fixture")
			}
			write := func(name, body string) string {
				t.Helper()
				path := filepath.Join(t.TempDir(), name)
				if err := os.WriteFile(path, []byte(body), 0600); err != nil {
					t.Fatal(err)
				}
				return path
			}
			var reversal string
			if err := p.f.observer.QueryRow(ctx, "SELECT payload FROM crawler_ownership_reversal WHERE reversal_sha256=$1", plan.document.ReversalSHA256).Scan(&reversal); err != nil {
				t.Fatal(err)
			}
			request, _ := json.Marshal(plan.Request())
			intent, _ := json.Marshal(p.spec)
			env := map[string]string{
				"ORDINARY_GO_WORKER_MODE": "cold-ordinary-finalization-inspect", "ORDINARY_OWNERSHIP_SOURCE_REVISION": p.spec.SourceRevision, "LOCAL_DATABASE_URL": p.f.dsn,
				"ORDINARY_COLD_ROUTING_EPOCH": strconv.FormatInt(p.plan.Epoch(), 10), "ORDINARY_COLD_RETIREMENT_EPOCH": strconv.FormatInt(plan.RetirementEpoch(), 10),
				"ORDINARY_COLD_INTENT_FILE": write("intent.json", string(intent)), "ORDINARY_COLD_INTENT_SHA256": p.intent,
				"ORDINARY_COLD_REVERSAL_FILE": write("reversal.json", reversal), "ORDINARY_COLD_REVERSAL_SHA256": plan.document.ReversalSHA256,
				"ORDINARY_COLD_PLAN_SHA256": p.plan.digest, "ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256": plan.Request().OrdinaryRestorationPlanSHA256,
				"ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256": plan.Request().B0ReactivationPlanSHA256,
				"ORDINARY_COLD_FINALIZATION_REQUEST_FILE":   write("request.json", string(request)), "ORDINARY_COLD_FINALIZATION_REQUEST_SHA256": coldForwardBytesDigest(string(request)),
				"ORDINARY_COLD_FINALIZATION_PLAN_SHA256": plan.digest,
			}
			type identity struct {
				Source         string          `json:"source_revision"`
				PlanSHA        string          `json:"ordinary_finalization_plan_sha256"`
				Plan           json.RawMessage `json:"ordinary_finalization_plan"`
				Phase          string          `json:"ordinary_finalization_phase"`
				ReceiptSHA     string          `json:"ordinary_finalization_receipt_sha256"`
				Receipt        json.RawMessage `json:"ordinary_finalization_receipt"`
				RestoredSource string          `json:"restored_ordinary_source_revision"`
			}
			call := func(accepted bool) identity {
				t.Helper()
				bounded, cancel := context.WithTimeout(ctx, 4*time.Second)
				defer cancel()
				cmd := exec.CommandContext(bounded, binary, "--cold-ordinary-finalization-inspect")
				for key, value := range env {
					cmd.Env = append(cmd.Env, key+"="+value)
				}
				output, err := cmd.CombinedOutput()
				if !accepted {
					if err == nil || strings.Contains(string(output), p.f.dsn) || !strings.Contains(string(output), "ordinary cold coordinator") {
						t.Fatal("actual inspection CLI admitted drift or leaked input")
					}
					return identity{}
				}
				var result identity
				if err != nil || json.Unmarshal(output, &result) != nil || result.Source != p.spec.SourceRevision || result.PlanSHA != plan.digest || string(result.Plan) != plan.body {
					t.Fatal("actual historical CLI lost retained identity", err)
				}
				return result
			}
			if got := call(true); got.Phase != "prepared" || got.ReceiptSHA != "" {
				t.Fatal("CLI invented completion")
			}
			for key, value := range map[string]string{"ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("c", 40), "ORDINARY_COLD_FINALIZATION_REQUEST_SHA256": strings.Repeat("f", 64), "ORDINARY_COLD_FINALIZATION_PLAN_SHA256": strings.Repeat("f", 64), "ORDINARY_COLD_RETIREMENT_EPOCH": strconv.FormatInt(plan.RetirementEpoch()+1, 10), "ORDINARY_COLD_B0_LUA_FILE": "/private/unused.lua"} {
				old := env[key]
				env[key] = value
				call(false)
				env[key] = old
			}
			if _, err := publishColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
				t.Fatal(err)
			}
			state, err := completeColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := p.f.observer.Exec(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')"); err != nil {
				t.Fatal(err)
			}
			before := forwardRedisSnapshot(t, p.f.client)
			tx, err := p.f.observer.Begin(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer tx.Rollback(ctx)
			if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1),pg_advisory_xact_lock($2)", OrdinaryLeaseBarrier, routingEpochBarrier); err != nil {
				t.Fatal(err)
			}
			got := call(true)
			if got.Phase != "complete" || got.ReceiptSHA != state.digest || string(got.Receipt) != state.body {
				t.Fatal("CLI adopted current allocator or lost completion")
			}
			if native && got.RestoredSource != strings.Repeat("b", 40) {
				t.Fatal("CLI changed prior ordinary source")
			}
			if !native && got.RestoredSource != "" {
				t.Fatal("CLI invented prior native owner")
			}
			if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) {
				t.Fatal("historical CLI changed routing state")
			}
		})
	}
}
