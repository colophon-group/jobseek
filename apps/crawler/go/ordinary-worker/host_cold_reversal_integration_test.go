package worker

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Actual private PostgreSQL/Redis and journal execution. Host, release and
// listener labels remain fixtures; this does not admit a production reversal.
func TestRealHostColdReversalJournalBindsReservedSourceAndRecoversRetirement(t *testing.T) {
	f, d, state, previous, prepared, luaSHA := hostPhaseTestFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 40*time.Second)
	defer cancel()
	canonical, before := coldExecutableCanonicalSnapshot(t, f), fullColdExecutableRedisSnapshot(t, f)
	var info HostColdPhaseContext
	call := func(r HostColdPhaseRequest, hook func(string) error) (*HostColdPhaseResult, error) {
		t.Helper()
		sha := hostPhaseRetainTestRequest(t, state, r)
		var result *HostColdPhaseResult
		err := runHostPhaseTestDriver(ctx, state, f.pg, d, func(scoped context.Context, store *hostStore) error {
			var err error
			info, err = InspectHostColdPhaseContext(scoped, f.pg)
			if err != nil {
				return err
			}
			result, err = runHostColdPhase(scoped, f.pg, sha, hook)
			return err
		})
		return result, err
	}
	r := HostColdPhaseRequest{Version: "jobseek.crawler-host-cold-request/v2", Binding: d.Binding, Operation: "cold-b0-target", PreviousEpoch: previous, LuaSHA256: luaSHA, Namespace: "host-reversal", Shard: "lightpanda-b0", Cohort: "c1"}
	target, err := call(r, nil)
	if err != nil || target == nil || target.Outcome != "completed" {
		t.Fatal("initial target", err)
	}
	intentBody := hostPhaseTestIntent(t, info, previous, prepared, target)
	intent := hostPhaseRetainTestInput(t, state, intentBody)
	r = HostColdPhaseRequest{Version: r.Version, Binding: d.Binding, PredecessorSHA256: hostPhaseResultSHA(t, target), Operation: "cold-begin", PreviousEpoch: previous, IntentSHA256: intent, TargetSHA256: target.Native.B0TargetSHA256, LuaSHA256: luaSHA}
	begun, err := call(r, nil)
	if err != nil || begun == nil || begun.Outcome != "completed" {
		t.Fatal("initial intent", err)
	}
	r.Operation, r.TargetSHA256, r.LuaSHA256, r.PredecessorSHA256 = "cold-reserve", "", "", hostPhaseResultSHA(t, begun)
	reserved, err := call(r, nil)
	if err != nil || reserved == nil || reserved.Outcome != "completed" {
		t.Fatal("initial reservation", err)
	}
	r.Operation, r.PredecessorSHA256 = "cold-inspect", hostPhaseResultSHA(t, reserved)
	inspected, err := call(r, nil)
	if err != nil || inspected == nil || inspected.Outcome != "completed" {
		t.Fatal("initial inspection", err)
	}
	r.Operation, r.PredecessorSHA256 = "cold-reversal-begin", hostPhaseResultSHA(t, inspected)
	r.RoutingEpoch, r.PlanSHA256 = reserved.Native.RoutingEpoch, reserved.Native.PlanSHA256
	intentSpec, err := queue.DecodeColdTransitionSpec(string(intentBody), intent)
	if err != nil {
		t.Fatal("exact retained intent", err)
	}
	spec := queue.ColdReversalSpec{Version: "jobseek.crawler.cold-reversal/v1", ReversalID: fixtureID(t), ForwardIntentSHA256: intent, SourceRevision: d.Binding.SourceRevision, SourceEpoch: r.RoutingEpoch, SourcePlanSHA256: r.PlanSHA256, SourcePhase: "reserved", RollbackReleaseSHA256: info.RollbackReleaseSHA256, RollbackOrdinaryPlanSHA256: intentSpec.PreviousOrdinaryPlanSHA256, RollbackB0ReceiptSHA256: intentSpec.PreviousB0ReceiptSHA256, ColdAttestationSHA256: info.ColdAttestationSHA256}
	retain := func(spec queue.ColdReversalSpec) string {
		body, _ := json.Marshal(spec)
		return hostPhaseRetainTestInput(t, state, body)
	}
	for _, fault := range []string{"source_revision", "source_epoch", "source_plan_sha256", "forward_intent_sha256", "source_phase", "rollback_release_sha256", "rollback_ordinary_plan_sha256", "rollback_b0_receipt_sha256", "cold_attestation_sha256"} {
		badSpec := spec
		switch fault {
		case "source_revision":
			badSpec.SourceRevision = strings.Repeat("8", 40)
		case "source_epoch":
			badSpec.SourceEpoch++
		case "source_plan_sha256":
			badSpec.SourcePlanSHA256 = strings.Repeat("8", 64)
		case "forward_intent_sha256":
			badSpec.ForwardIntentSHA256 = strings.Repeat("8", 64)
		case "source_phase":
			badSpec.SourcePhase = "active"
		case "rollback_release_sha256":
			badSpec.RollbackReleaseSHA256 = strings.Repeat("8", 64)
		case "rollback_ordinary_plan_sha256":
			badSpec.RollbackOrdinaryPlanSHA256 = strings.Repeat("8", 64)
		case "rollback_b0_receipt_sha256":
			badSpec.RollbackB0ReceiptSHA256 = strings.Repeat("8", 64)
		case "cold_attestation_sha256":
			badSpec.ColdAttestationSHA256 = strings.Repeat("8", 64)
		}
		bad := r
		bad.ReversalSHA256 = retain(badSpec)
		if result, err := call(bad, nil); err == nil || result != nil {
			t.Fatal("substituted reversal authority", fault)
		}
		var last int64
		if f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&last) != nil || last != r.RoutingEpoch {
			t.Fatal("refusal changed allocator", fault)
		}
		var reversals int
		var phase string
		if f.pg.QueryRow(ctx, "SELECT count(*) FROM crawler_ownership_reversal WHERE forward_intent_sha256=$1", intent).Scan(&reversals) != nil || reversals != 0 || f.pg.QueryRow(ctx, "SELECT phase FROM crawler_ownership_transition WHERE intent_sha256=$1", intent).Scan(&phase) != nil || phase != "reserved" {
			t.Fatal("substitution refusal performed reversal effect", fault)
		}
	}
	r.ReversalSHA256 = retain(spec)
	interrupted := false
	interrupt := func(stage string) error {
		if stage == "native_effect_returned" {
			interrupted = true
			return errHostPreflight
		}
		return nil
	}
	if result, err := call(r, interrupt); err == nil || result != nil || !interrupted {
		t.Fatal("reversal effect seam did not interrupt")
	}
	reversal, err := call(r, nil)
	if err != nil || reversal == nil || reversal.Outcome != "completed" {
		t.Fatal("recover reversal intent", err)
	}
	beginRequest := r
	r.Operation, r.PredecessorSHA256 = "cold-reversal-reserve", hostPhaseResultSHA(t, reversal)
	interrupted = false
	if result, err := call(r, interrupt); err == nil || result != nil || !interrupted {
		t.Fatal("retirement effect seam did not interrupt")
	}
	retirement, err := call(r, nil)
	if err != nil || retirement == nil || retirement.Outcome != "completed" || retirement.Native.RetirementEpoch <= r.RoutingEpoch {
		t.Fatal("recover retirement", err)
	}
	reserveRequest := r
	r.Operation, r.PredecessorSHA256, r.RetirementEpoch = "cold-reversal-inspect", hostPhaseResultSHA(t, retirement), retirement.Native.RetirementEpoch
	bad := r
	bad.RetirementEpoch++
	if result, err := call(bad, nil); err == nil || result != nil {
		t.Fatal("adopted another retirement")
	}
	observation, err := call(r, nil)
	if err != nil || observation == nil || observation.Outcome != "completed" {
		t.Fatal("inspect exact retirement", err)
	}
	for _, pair := range []struct {
		request HostColdPhaseRequest
		result  *HostColdPhaseResult
	}{{beginRequest, reversal}, {reserveRequest, retirement}, {r, observation}} {
		actual, err := call(pair.request, nil)
		if err != nil || !reflect.DeepEqual(actual, pair.result) {
			t.Fatal("historical reversal retry changed result", err)
		}
	}
	var last int64
	if f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&last) != nil || last != r.RetirementEpoch {
		t.Fatal("retry reserved R+1")
	}
	if canonical != coldExecutableCanonicalSnapshot(t, f) || !reflect.DeepEqual(before, fullColdExecutableRedisSnapshot(t, f)) {
		t.Fatal("reversal admission changed canonical rows or Redis")
	}
	t.Log("actual private host journal refuses substituted reversal source/epoch/plan/intent/phase/rollback/cold evidence; reserved reversal and retirement effects recover after returned-effect interruption, exact historical retry keeps R without R+1 and conserves canonical rows/complete Redis; production host admission, complete restoration and SIGKILL reversal remain unproven")
}
