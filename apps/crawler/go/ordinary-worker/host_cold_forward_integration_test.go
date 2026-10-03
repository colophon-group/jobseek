package worker

import (
	"context"
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// The private library fixture has no production producer. This proves native
// peer absence cannot be replaced by retained journal/SQL data or a caller URL;
// it does not claim successful producer transfer/publication or host admission.
func TestRealHostColdForwardJournalRefusesSubstitutionAndMissingProducer(t *testing.T) {
	f, d, state, previous, prepared, luaSHA := hostPhaseTestFixture(t)
	if _, err := os.Lstat(b0producer.SocketPath); !errors.Is(err, fs.ErrNotExist) {
		t.Fatal("private refusal fixture requires absent fixed producer socket")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	canonical, before := coldExecutableCanonicalSnapshot(t, f), fullColdExecutableRedisSnapshot(t, f)
	var info HostColdPhaseContext
	call := func(r HostColdPhaseRequest) (*HostColdPhaseResult, error) {
		t.Helper()
		sha := hostPhaseRetainTestRequest(t, state, r)
		var result *HostColdPhaseResult
		err := runHostPhaseTestDriver(ctx, state, f.pg, d, func(scoped context.Context, store *hostStore) error {
			var err error
			info, err = InspectHostColdPhaseContext(scoped, f.pg)
			if err != nil {
				return err
			}
			result, err = RunHostColdPhase(scoped, f.pg, sha)
			return err
		})
		return result, err
	}
	r := HostColdPhaseRequest{Version: "jobseek.crawler-host-cold-request/v2", Binding: d.Binding, Operation: "cold-b0-target", PreviousEpoch: previous, LuaSHA256: luaSHA, Namespace: "host-forward-refusal", Shard: "lightpanda-b0", Cohort: "c1"}
	target, err := call(r)
	if err != nil || target.Outcome != "completed" {
		t.Fatal("native target", err)
	}
	intent := hostPhaseRetainTestInput(t, state, hostPhaseTestIntent(t, info, previous, prepared, target))
	r = HostColdPhaseRequest{Version: r.Version, Binding: d.Binding, PredecessorSHA256: hostPhaseResultSHA(t, target), Operation: "cold-begin", PreviousEpoch: previous, IntentSHA256: intent, TargetSHA256: target.Native.B0TargetSHA256, LuaSHA256: luaSHA}
	begun, err := call(r)
	if err != nil || begun.Outcome != "completed" {
		t.Fatal("native intent", err)
	}
	r.Operation, r.TargetSHA256, r.LuaSHA256, r.PredecessorSHA256 = "cold-reserve", "", "", hostPhaseResultSHA(t, begun)
	reserved, err := call(r)
	if err != nil || reserved.Outcome != "completed" {
		t.Fatal("native reservation", err)
	}
	r.Operation, r.PredecessorSHA256 = "cold-inspect", hostPhaseResultSHA(t, reserved)
	inspected, err := call(r)
	if err != nil || inspected.Outcome != "completed" {
		t.Fatal("native inspection", err)
	}
	forward := queue.ColdB0ForwardRequest{IntentSHA256: intent, SourceRevision: d.Binding.SourceRevision, RoutingEpoch: reserved.Native.RoutingEpoch, OrdinaryPlanSHA256: reserved.Native.PlanSHA256}
	retainForward := func(request queue.ColdB0ForwardRequest) string {
		body, _ := json.Marshal(request)
		return hostPhaseRetainTestInput(t, state, body)
	}
	r.Operation, r.PredecessorSHA256 = "cold-b0-forward-plan", hostPhaseResultSHA(t, inspected)
	r.RoutingEpoch, r.PlanSHA256 = forward.RoutingEpoch, forward.OrdinaryPlanSHA256
	r.TargetSHA256, r.LuaSHA256, r.ForwardRequestSHA256 = target.Native.B0TargetSHA256, luaSHA, retainForward(forward)
	for _, fault := range []string{"source", "request epoch", "request plan", "request intent", "skipped inspection", "target", "Lua"} {
		bad, request := r, forward
		switch fault {
		case "source":
			request.SourceRevision = strings.Repeat("8", 40)
		case "request epoch":
			request.RoutingEpoch++
		case "request plan":
			request.OrdinaryPlanSHA256 = strings.Repeat("8", 64)
		case "request intent":
			request.IntentSHA256 = strings.Repeat("8", 64)
		case "skipped inspection":
			bad.PredecessorSHA256 = hostPhaseResultSHA(t, reserved)
		case "target":
			bad.TargetSHA256 = strings.Repeat("8", 64)
		case "Lua":
			bad.LuaSHA256 = strings.Repeat("8", 64)
		}
		bad.ForwardRequestSHA256 = retainForward(request)
		if result, err := call(bad); err == nil || result != nil {
			t.Fatal("substituted forward authority", fault)
		}
	}
	result, err := call(r)
	if err != nil || result == nil || result.Outcome != "unresolved" || result.Native != nil {
		t.Fatal("absent actual producer adopted fabricated authority", err)
	}
	if retry, err := call(r); err != nil || !reflect.DeepEqual(retry, result) {
		t.Fatal("historical unresolved retry changed", err)
	}
	advance := r
	advance.Operation, advance.ForwardPlanSHA256, advance.PredecessorSHA256 = "cold-b0-forward-retain", strings.Repeat("8", 64), hostPhaseResultSHA(t, result)
	if next, err := call(advance); err == nil || next != nil {
		t.Fatal("unresolved producer refusal authorized transfer retention")
	}
	r.PredecessorSHA256 = hostPhaseResultSHA(t, result)
	if retried, err := call(r); err != nil || retried.Outcome != "unresolved" || retried.Native != nil {
		t.Fatal("separate exact native retry failed", err)
	}
	var epoch int64
	var transfers int
	if f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epoch) != nil || epoch != forward.RoutingEpoch || f.pg.QueryRow(ctx, "SELECT count(*) FROM crawler_ownership_b0_forward").Scan(&transfers) != nil || transfers != 0 {
		t.Fatal("refusal changed allocator or retained a transfer")
	}
	if canonical != coldExecutableCanonicalSnapshot(t, f) || !reflect.DeepEqual(before, fullColdExecutableRedisSnapshot(t, f)) {
		t.Fatal("refusal changed canonical or complete Redis values")
	}
	t.Log("actual private PostgreSQL/Redis forward journal joined original completed reservation and intent/target/Lua ancestry; substituted typed request/source/epoch/plan and skipped inspection refused before effects; absent fixed native producer retained unresolved outcome, exact retry conserved epoch and complete Redis values, no transfer/publication admission")
}
