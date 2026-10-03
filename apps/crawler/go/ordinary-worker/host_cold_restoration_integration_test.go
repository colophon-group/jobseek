package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealHostColdRestorationJournalKeepsExactRetirementAndRecoversEffects(t *testing.T) {
	f, d, state, previous, prepared, luaSHA := hostPhaseTestFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Second)
	defer cancel()
	// Seed only this private Redis through the reviewed Lua. This is an empty
	// historical B0 queue with a synthetic prior receipt, not a live producer.
	lua, err := os.ReadFile("../../src/lua/lightpanda_b0_queue.lua")
	if err != nil {
		t.Fatal(err)
	}
	keys := []string{}
	for _, suffix := range []string{"route", "records", "ready", "inflight", "dead", "terminal", "origin-holders"} {
		keys = append(keys, "lightpanda-b0:{host-restoration}:"+suffix)
	}
	args := []any{"initialize_producer", "lightpanda-b0", fmt.Sprint(previous), "go", "", "0", "", "0", "0", "0", "", "", "", "64", "2.0", "", "0", "host-restoration", "", "0", "c1", 1, "0", "browser-use-careers"}
	if reply, err := f.r.Eval(ctx, string(lua), keys, args...).Slice(); err != nil || len(reply) != 12 || reply[0] != "accepted" {
		t.Fatal("private source queue initialization", err)
	}
	canonical := coldExecutableCanonicalSnapshot(t, f)
	var info HostColdPhaseContext
	type pair struct {
		request HostColdPhaseRequest
		result  *HostColdPhaseResult
	}
	var history []pair
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
	complete := func(r HostColdPhaseRequest) *HostColdPhaseResult {
		t.Helper()
		result, err := call(r, nil)
		if err != nil || result == nil || result.Outcome != "completed" {
			t.Fatal("complete exact phase", r.Operation, err)
		}
		history = append(history, pair{r, result})
		return result
	}
	retain := func(value any) string {
		body, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		return hostPhaseRetainTestInput(t, state, body)
	}
	r := HostColdPhaseRequest{Version: "jobseek.crawler-host-cold-request/v2", Binding: d.Binding, Operation: "cold-b0-target", PreviousEpoch: previous, LuaSHA256: luaSHA, Namespace: "host-restoration", Shard: "lightpanda-b0", Cohort: "c1"}
	target := complete(r)
	var intentSpec queue.ColdTransitionSpec
	if json.Unmarshal(hostPhaseTestIntent(t, info, previous, prepared, target), &intentSpec) != nil {
		t.Fatal("fixture intent")
	}
	intentSpec.PreviousB0ReceiptSHA256 = strings.Repeat("7", 64)
	intent := retain(intentSpec)
	r = HostColdPhaseRequest{Version: r.Version, Binding: d.Binding, PredecessorSHA256: hostPhaseResultSHA(t, target), Operation: "cold-begin", PreviousEpoch: previous, IntentSHA256: intent, TargetSHA256: target.Native.B0TargetSHA256, LuaSHA256: luaSHA}
	begun := complete(r)
	r.Operation, r.TargetSHA256, r.LuaSHA256, r.PredecessorSHA256 = "cold-reserve", "", "", hostPhaseResultSHA(t, begun)
	reserved := complete(r)
	r.Operation, r.PredecessorSHA256 = "cold-inspect", hostPhaseResultSHA(t, reserved)
	inspected := complete(r)
	r.Operation, r.PredecessorSHA256, r.RoutingEpoch, r.PlanSHA256 = "cold-reversal-begin", hostPhaseResultSHA(t, inspected), reserved.Native.RoutingEpoch, reserved.Native.PlanSHA256
	r.ReversalSHA256 = retain(queue.ColdReversalSpec{Version: "jobseek.crawler.cold-reversal/v1", ReversalID: fixtureID(t), ForwardIntentSHA256: intent, SourceRevision: d.Binding.SourceRevision, SourceEpoch: r.RoutingEpoch, SourcePlanSHA256: r.PlanSHA256, SourcePhase: "reserved", RollbackReleaseSHA256: info.RollbackReleaseSHA256, RollbackB0ReceiptSHA256: intentSpec.PreviousB0ReceiptSHA256, ColdAttestationSHA256: info.ColdAttestationSHA256})
	reversal := complete(r)
	r.Operation, r.PredecessorSHA256 = "cold-reversal-reserve", hostPhaseResultSHA(t, reversal)
	retired := complete(r)
	r.Operation, r.PredecessorSHA256, r.RetirementEpoch = "cold-reversal-inspect", hostPhaseResultSHA(t, retired), retired.Native.RetirementEpoch
	retirement := complete(r)
	r.Operation, r.PredecessorSHA256, r.TargetSHA256, r.LuaSHA256 = "cold-b0-rollback-plan", hostPhaseResultSHA(t, retirement), target.Native.B0TargetSHA256, luaSHA
	restore := queue.ColdB0RollbackRequest{ReversalSHA256: r.ReversalSHA256, SourceRevision: d.Binding.SourceRevision, RetirementEpoch: r.RetirementEpoch, B0SourceEpoch: previous, SourceReceiptSHA256: intentSpec.PreviousB0ReceiptSHA256}
	before := fullColdExecutableRedisSnapshot(t, f)
	for _, fault := range []string{"source", "retirement", "receipt", "candidate without transfer", "target", "Lua"} {
		bad, request := r, restore
		switch fault {
		case "source":
			request.SourceRevision = strings.Repeat("8", 40)
		case "retirement":
			request.RetirementEpoch++
		case "receipt":
			request.SourceReceiptSHA256 = strings.Repeat("8", 64)
		case "candidate without transfer":
			request.B0SourceEpoch = r.RoutingEpoch
		case "target":
			bad.TargetSHA256 = strings.Repeat("8", 64)
		case "Lua":
			bad.LuaSHA256 = strings.Repeat("8", 64)
		}
		bad.RestoreRequestSHA256 = retain(request)
		if result, err := call(bad, nil); err == nil || result != nil {
			t.Fatal("substituted restoration admitted", fault)
		}
		var count int
		if f.pg.QueryRow(ctx, "SELECT count(*) FROM crawler_ownership_b0_restoration WHERE reversal_sha256=$1", r.ReversalSHA256).Scan(&count) != nil || count != 0 || !reflect.DeepEqual(before, fullColdExecutableRedisSnapshot(t, f)) {
			t.Fatal("refusal performed restoration effects", fault)
		}
	}
	r.RestoreRequestSHA256 = retain(restore)
	plan := complete(r)
	r.Operation, r.PredecessorSHA256, r.B0RollbackPlanSHA256 = "cold-b0-rollback-retain", hostPhaseResultSHA(t, plan), plan.Native.B0RollbackPlanSHA256
	retained := complete(r)
	r.Operation, r.PredecessorSHA256 = "cold-b0-rollback-restore", hostPhaseResultSHA(t, retained)
	interrupted := false
	interrupt := func(stage string) error {
		if stage == "native_effect_returned" {
			interrupted = true
			return errHostPreflight
		}
		return nil
	}
	if result, err := call(r, interrupt); err == nil || result != nil || !interrupted {
		t.Fatal("restoration returned-effect interruption did not fire")
	}
	restored := complete(r)
	if restored.Native.B0RestorationPhase != "fences-cleared" {
		t.Fatal("source fences not cleared")
	}
	r.Operation, r.PredecessorSHA256, r.TargetSHA256, r.LuaSHA256 = "cold-b0-rollback-inspect", hostPhaseResultSHA(t, restored), "", ""
	observed := complete(r)
	r.Operation, r.PredecessorSHA256, r.TargetSHA256, r.LuaSHA256, r.RestoreRequestSHA256 = "cold-ordinary-rollback-plan", hostPhaseResultSHA(t, observed), target.Native.B0TargetSHA256, luaSHA, ""
	ordinary := queue.ColdOrdinaryRestorationRequest{ReversalSHA256: r.ReversalSHA256, SourceRevision: d.Binding.SourceRevision, RetirementEpoch: r.RetirementEpoch, B0RestorationPlanSHA256: r.B0RollbackPlanSHA256}
	badOrdinary := ordinary
	badOrdinary.RollbackSourceRevision = strings.Repeat("8", 40)
	bad := r
	bad.OrdinaryRequestSHA256 = retain(badOrdinary)
	if result, err := call(bad, nil); err == nil || result != nil {
		t.Fatal("native ordinary mode substituted for original legacy decision")
	}
	r.OrdinaryRequestSHA256 = retain(ordinary)
	ordinaryPlan := complete(r)
	if ordinaryPlan.Native.OrdinaryRestorationMode != "legacy" {
		t.Fatal("original legacy ordinary decision lost")
	}
	r.Operation, r.PredecessorSHA256, r.OrdinaryRestorationPlanSHA256 = "cold-ordinary-rollback-retain", hostPhaseResultSHA(t, ordinaryPlan), ordinaryPlan.Native.OrdinaryRestorationPlanSHA256
	interrupted = false
	if result, err := call(r, interrupt); err == nil || result != nil || !interrupted {
		t.Fatal("ordinary retention returned-effect interruption did not fire")
	}
	ordinaryRetained := complete(r)
	r.Operation, r.PredecessorSHA256, r.TargetSHA256, r.LuaSHA256 = "cold-ordinary-rollback-inspect", hostPhaseResultSHA(t, ordinaryRetained), "", ""
	complete(r)
	after := fullColdExecutableRedisSnapshot(t, f)
	allKeys := map[string]bool{}
	for key := range before {
		allKeys[key] = true
	}
	for key := range after {
		allKeys[key] = true
	}
	for key := range allKeys {
		if key != keys[0] && key != "lightpanda-b0:producer-owner" && before[key] != after[key] {
			t.Fatal("restoration changed unrelated Redis value/type/expiry", key)
		}
	}
	owner, err := f.r.HGetAll(ctx, "lightpanda-b0:producer-owner").Result()
	if err != nil || len(owner) != 8 || owner["rollback_plan_digest"] != r.B0RollbackPlanSHA256 || owner["source_receipt_sha256"] != restore.SourceReceiptSHA256 || owner["routing_epoch"] != fmt.Sprint(previous) || owner["namespace"] != "host-restoration" {
		t.Fatal("exact historical producer tombstone missing")
	}
	if exists, err := f.r.Exists(ctx, keys...).Result(); err != nil || exists != 0 {
		t.Fatal("historical native queue not retired")
	}
	for _, item := range history {
		actual, err := call(item.request, nil)
		if err != nil || !reflect.DeepEqual(actual, item.result) {
			t.Fatal("historical exact retry changed phase", item.request.Operation, err)
		}
	}
	var last int64
	if f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&last) != nil || last != r.RetirementEpoch || !reflect.DeepEqual(after, fullColdExecutableRedisSnapshot(t, f)) || canonical != coldExecutableCanonicalSnapshot(t, f) {
		t.Fatal("exact retries changed R, canonical rows or complete Redis values")
	}
	// Restoration intentionally replaces the source route with its tombstone.
	if reflect.DeepEqual(before, after) {
		t.Fatal("restoration never changed source Redis")
	}
	t.Log("actual private host journal completes seven B0/ordinary restoration stages from reserved N at exact R; altered source/retirement/receipt/target/Lua/native mode refuse before effects, restoration and ordinary retention returned-effect interruption recover, all fourteen historical phases preserve R/canonical rows/complete Redis values; empty historical B0 queue and synthetic prior receipt/host authority only, nonempty transfer, SIGKILL restoration, reactivation/finalization/startup/runtime admission remain unproven")
}
