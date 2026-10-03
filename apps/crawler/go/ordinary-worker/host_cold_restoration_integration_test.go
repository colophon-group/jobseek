package worker

import (
	"context"
	"crypto/sha1"
	"encoding/hex"
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
	runHostColdRestorationJournalTest(t, false)
}

func TestRealHostColdRestorationNonemptyJournalRestoresCanonicalSchedule(t *testing.T) {
	runHostColdRestorationJournalTest(t, true)
}

func runHostColdRestorationJournalTest(t *testing.T, nonempty bool) {
	t.Helper()
	f, d, state, previous, prepared, luaSHA := hostPhaseTestFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Second)
	defer cancel()
	// Seed only this private Redis through the reviewed lifecycle Lua. The prior
	// receipt and host labels remain synthetic; no producer runs on this host.
	lua, err := os.ReadFile("../../src/lua/lightpanda_b0_queue.lua")
	if err != nil {
		t.Fatal(err)
	}
	keys := []string{}
	for _, suffix := range []string{"route", "records", "ready", "inflight", "dead", "terminal", "origin-holders"} {
		keys = append(keys, "lightpanda-b0:{host-restoration}:"+suffix)
	}
	posting := ""
	if nonempty {
		var board string
		if f.pg.QueryRow(ctx, "SELECT id::text FROM job_board WHERE company_id=$1::uuid AND board_slug='browser-use-careers'", f.company).Scan(&board) != nil {
			t.Fatal("owned B0 board")
		}
		posting = seedExecutableColdB0InNamespace(t, f, board, previous, lua, "host-restoration")
		claimArgs := []any{"claim_next", "lightpanda-b0", fmt.Sprint(previous), "go", "", "0", fmt.Sprint(time.Now().UnixMilli()), "600000", "0", "0", "", "", "", "64", "2.0", "", "0", "host-restoration", "", "0", "c1", 1, "0", "browser-use-careers"}
		claim, err := f.r.Eval(ctx, string(lua), keys, claimArgs...).Slice()
		if err != nil || len(claim) != 12 || claim[0] != "accepted" || claim[3] != posting {
			t.Fatal("owned task claim", err)
		}
		if _, err := f.pg.Exec(ctx, "SELECT public.jobseek_lightpanda_b0_activate_write_fence($1::uuid,$2,$3,'go',$4,$5,$6)", posting, "lightpanda-b0", previous, int64(3), claim[7], claim[4]); err != nil {
			t.Fatal("owned historical fence", err)
		}
		claimArgs[0], claimArgs[4], claimArgs[5], claimArgs[6], claimArgs[11], claimArgs[16] = "complete", claim[3], claim[6], claim[4], claim[7], claim[5]
		payload, ok := claim[8].(string)
		if !ok {
			t.Fatal("owned task payload")
		}
		hash := sha1.Sum([]byte(payload))
		claimArgs[12] = hex.EncodeToString(hash[:])
		if result, err := f.r.Eval(ctx, string(lua), keys, claimArgs...).Slice(); err != nil || len(result) != 12 || result[0] != "accepted" {
			t.Fatal("owned historical task completion", err)
		}
		// This private fixture owns the origin and has completed its only
		// claimant. Remove the short claim throttle before the permanent
		// conservation baseline; its natural expiry must not look like a
		// restoration effect on a slower runner.
		if f.r.HLen(ctx, keys[6]).Val() != 0 || f.r.ZCard(ctx, keys[3]).Val() != 0 || f.r.Del(ctx, "ratelimit:jobs.example.test").Err() != nil {
			t.Fatal("owned historical origin release")
		}
	} else {
		args := []any{"initialize_producer", "lightpanda-b0", fmt.Sprint(previous), "go", "", "0", "", "0", "0", "0", "", "", "", "64", "2.0", "", "0", "host-restoration", "", "0", "c1", 1, "0", "browser-use-careers"}
		if reply, err := f.r.Eval(ctx, string(lua), keys, args...).Slice(); err != nil || len(reply) != 12 || reply[0] != "accepted" {
			t.Fatal("private source queue initialization", err)
		}
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
	priorReceipt := []byte(fmt.Sprintf("schema=jobseek.lightpanda-b0-active/v1\nstate=active\ncohort=c1\nnamespace=host-restoration\nshard_id=lightpanda-b0\nrouting_epoch=%d\nplan_digest=%s\ncompose_digest=%s\ncrawler_image_ref=ghcr.io/colophon-group/jobseek-crawler@sha256:%s\ndeploy_revision=%s\nactivated_at_epoch=1\n", previous, strings.Repeat("7", 64), strings.Repeat("6", 64), strings.Repeat("5", 64), d.Binding.SourceRevision))
	intentSpec.PreviousB0ReceiptSHA256 = hostPhaseRetainTestInput(t, state, priorReceipt)
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
	ordinaryInspection := complete(r)
	beforeMissingProducer := fullColdExecutableRedisSnapshot(t, f)
	completion := r
	completion.Operation, completion.PredecessorSHA256 = "cold-b0-reactivation-plan", hostPhaseResultSHA(t, ordinaryInspection)
	completion.TargetSHA256, completion.LuaSHA256, completion.PriorB0ReceiptSHA256 = target.Native.B0TargetSHA256, luaSHA, intentSpec.PreviousB0ReceiptSHA256
	completion.OrdinaryRequestSHA256, completion.B0RollbackPlanSHA256 = "", ""
	missing, err := call(completion, nil)
	if err != nil || missing == nil || missing.Outcome != "unresolved" || missing.Native != nil {
		t.Fatal("missing fixed producer granted reactivation", err)
	}
	badCompletion := completion
	badCompletion.Operation, badCompletion.PredecessorSHA256, badCompletion.B0ReactivationPlanSHA256 = "cold-b0-reactivation-retain", hostPhaseResultSHA(t, missing), strings.Repeat("8", 64)
	if result, err := call(badCompletion, nil); err == nil || result != nil {
		t.Fatal("unresolved producer effect authorized successor")
	}
	completion.PredecessorSHA256 = hostPhaseResultSHA(t, missing)
	retry, err := call(completion, nil)
	if err != nil || retry == nil || retry.Outcome != "unresolved" || retry.Native != nil || !reflect.DeepEqual(beforeMissingProducer, fullColdExecutableRedisSnapshot(t, f)) {
		t.Fatal("missing producer exact retry changed queue or granted authority", err)
	}
	after := fullColdExecutableRedisSnapshot(t, f)
	allKeys := map[string]bool{}
	for key := range before {
		allKeys[key] = true
	}
	for key := range after {
		allKeys[key] = true
	}
	for key := range allKeys {
		allowed := key == keys[0] || key == "lightpanda-b0:producer-owner"
		if nonempty {
			for _, nativeKey := range keys {
				allowed = allowed || key == nativeKey
			}
			for _, legacyKey := range []string{"lightpanda-b0:legacy-guard", "scrape:" + posting, "scrapes_browser:jobs.example.test", "ready:browser:2"} {
				allowed = allowed || key == legacyKey
			}
		}
		if !allowed && before[key] != after[key] {
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
	afterRetry := fullColdExecutableRedisSnapshot(t, f)
	if f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&last) != nil || last != r.RetirementEpoch || !reflect.DeepEqual(after, afterRetry) || canonical != coldExecutableCanonicalSnapshot(t, f) {
		t.Fatal("exact retries changed R, canonical rows or complete Redis values", "changed keys", coldSnapshotChangedKeys(after, afterRetry))
	}
	// Restoration intentionally replaces the source route with its tombstone.
	if reflect.DeepEqual(before, after) {
		t.Fatal("restoration never changed source Redis")
	}
	if nonempty {
		var fences int
		if f.pg.QueryRow(ctx, "SELECT count(*) FROM lightpanda_b0_write_fence WHERE job_posting_id=$1::uuid", posting).Scan(&fences) != nil || fences != 0 {
			t.Fatal("exact historical SQL task fence not cleared")
		}
		score, err := f.r.ZScore(ctx, "scrapes_browser:jobs.example.test", posting).Result()
		if err != nil || score != 1925089445.100001 {
			t.Fatal("canonical recurring microsecond schedule lost", err)
		}
		configuration, err := f.r.HGetAll(ctx, "scrape:"+posting).Result()
		if err != nil || len(configuration) != 6 || configuration["description_r2_hash"] != "-9223372036854775808" || configuration["scrape_interval_hours"] != "24" {
			t.Fatal("restored legacy configuration lost SQL bigint/interval truth")
		}
		if members, err := f.r.ZRange(ctx, "scrapes_browser:jobs.example.test", 0, -1).Result(); err != nil || !reflect.DeepEqual(members, []string{posting}) {
			t.Fatal("restoration lost or duplicated queued posting")
		}
		t.Log("actual private host journal restores nonempty historical B0 work to exact canonical recurring microsecond schedule, SQL bigint hash and interval; native queue/guard retired, unrelated Redis values and canonical SQL conserved; interrupted restore/retention and fourteen historical retries keep R without replay or R+1; synthetic prior receipt/host authority, complete reactivation/finalization/installed host lifecycle remain unproven")
		return
	}
	t.Log("actual private completion journal binds canonical prior B0 receipt and restored ordinary plan at exact R; missing fixed producer retains unresolved reactivation and permits only exact retry, successor refuses and queue/canonical/epoch remain conserved; positive reactivation/finalization and full host runtime admission unproven")
	t.Log("actual private host journal completes seven B0/ordinary restoration stages from reserved N at exact R; altered source/retirement/receipt/target/Lua/native mode refuse before effects, restoration and ordinary retention returned-effect interruption recover, all fourteen historical phases preserve R/canonical rows/complete Redis values; empty historical B0 queue and synthetic prior receipt/host authority only, nonempty transfer, SIGKILL restoration, reactivation/finalization/startup/runtime admission remain unproven")
}
