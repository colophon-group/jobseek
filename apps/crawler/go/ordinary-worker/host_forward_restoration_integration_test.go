//go:build integration

package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type hostForwardRestorationProof struct {
	RetirementEpoch  int64             `json:"retirement_epoch"`
	ReversalSHA      string            `json:"reversal_sha256"`
	B0PlanSHA        string            `json:"b0_restoration_plan_sha256"`
	OrdinaryPlanSHA  string            `json:"ordinary_restoration_plan_sha256"`
	SourceReceiptSHA string            `json:"source_transfer_receipt_sha256"`
	RedisAfter       map[string]string `json:"redis_after"`
	SentinelSHA      string            `json:"candidate_sentinel_sha256_retained"`
}

func hostForwardRestorationPriorReceipt(source string, previous int64) []byte {
	// Deliberately synthetic prior E host evidence, bound as data in the intent.
	// The candidate N transfer receipt comes from the actual native journal.
	return []byte(fmt.Sprintf("schema=jobseek.lightpanda-b0-active/v1\nstate=active\ncohort=c1\nnamespace=host-selected-redis\nshard_id=lightpanda-b0\nrouting_epoch=%d\nplan_digest=%s\ncompose_digest=%s\ncrawler_image_ref=ghcr.io/colophon-group/jobseek-crawler@sha256:%s\ndeploy_revision=%s\nactivated_at_epoch=1\n", previous, strings.Repeat("7", 64), strings.Repeat("6", 64), strings.Repeat("5", 64), source))
}

func runHostForwardRestoration(t *testing.T, ctx context.Context, f nativePipelineFixture, state string, info HostColdPhaseContext, target, active *HostColdPhaseResult, intent string, previous int64, seed *hostForwardFixtureSeed, before map[string]string, call func(HostColdPhaseRequest) *HostColdPhaseResult) *hostForwardRestorationProof {
	t.Helper()
	if active.Native.Operation != "cold-forward-activate" || !planPattern.MatchString(active.Native.B0ForwardReceiptSHA256) || CheckHostMutationScope(ctx) != nil {
		t.Fatal("candidate active reversal anchor")
	}
	var phase, plan string
	var epoch int64
	if f.pg.QueryRow(ctx, "SELECT phase,routing_epoch,reserved_plan_sha256 FROM crawler_ownership_transition WHERE intent_sha256=$1 AND source_revision=$2", intent, info.Binding.SourceRevision).Scan(&phase, &epoch, &plan) != nil || phase != "active" || epoch != active.Native.RoutingEpoch || plan != active.Native.PlanSHA256 {
		t.Fatal("live candidate active SQL anchor")
	}
	sentinelPath := "/run/jobseek-lightpanda-producer/.activation-v1"
	sentinel, err := os.ReadFile(sentinelPath)
	if err != nil || len(sentinel) == 0 || len(sentinel) > 4096 {
		t.Fatal("owned candidate sentinel must remain explicit", err)
	}
	retain := func(value any) string {
		b, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		return hostPhaseRetainTestInput(t, state, b)
	}
	reversal := queue.ColdReversalSpec{Version: "jobseek.crawler.cold-reversal/v1", ReversalID: fixtureID(t), ForwardIntentSHA256: intent, SourceRevision: info.Binding.SourceRevision, SourceEpoch: active.Native.RoutingEpoch, SourcePlanSHA256: active.Native.PlanSHA256, SourcePhase: "active", RollbackReleaseSHA256: info.RollbackReleaseSHA256, RollbackB0ReceiptSHA256: hostDigest(hostForwardRestorationPriorReceipt(info.Binding.SourceRevision, previous)), ColdAttestationSHA256: info.ColdAttestationSHA256}
	req := HostColdPhaseRequest{Operation: "cold-reversal-begin", PredecessorSHA256: hostPhaseResultSHA(t, active), IntentSHA256: intent, RoutingEpoch: active.Native.RoutingEpoch, PlanSHA256: active.Native.PlanSHA256, ReversalSHA256: retain(reversal)}
	begun := call(req)
	req.Operation, req.PredecessorSHA256 = "cold-reversal-reserve", hostPhaseResultSHA(t, begun)
	retired := call(req)
	req.Operation, req.PredecessorSHA256, req.RetirementEpoch = "cold-reversal-inspect", hostPhaseResultSHA(t, retired), retired.Native.RetirementEpoch
	observed := call(req)
	restore := queue.ColdB0RollbackRequest{ReversalSHA256: req.ReversalSHA256, SourceRevision: info.Binding.SourceRevision, RetirementEpoch: req.RetirementEpoch, B0SourceEpoch: active.Native.RoutingEpoch, SourceReceiptSHA256: active.Native.B0ForwardReceiptSHA256}
	req.Operation, req.PredecessorSHA256, req.TargetSHA256, req.LuaSHA256 = "cold-b0-rollback-plan", hostPhaseResultSHA(t, observed), target.Native.B0TargetSHA256, hostDigest(mustHostForwardLua(t))
	req.RestoreRequestSHA256 = retain(restore)
	planned := call(req)
	req.Operation, req.PredecessorSHA256, req.B0RollbackPlanSHA256 = "cold-b0-rollback-retain", hostPhaseResultSHA(t, planned), planned.Native.B0RollbackPlanSHA256
	retained := call(req)
	req.Operation, req.PredecessorSHA256 = "cold-b0-rollback-restore", hostPhaseResultSHA(t, retained)
	restored := call(req)
	req.Operation, req.PredecessorSHA256, req.TargetSHA256, req.LuaSHA256 = "cold-b0-rollback-inspect", hostPhaseResultSHA(t, restored), "", ""
	inspected := call(req)
	ordinary := queue.ColdOrdinaryRestorationRequest{ReversalSHA256: req.ReversalSHA256, SourceRevision: info.Binding.SourceRevision, RetirementEpoch: req.RetirementEpoch, B0RestorationPlanSHA256: req.B0RollbackPlanSHA256}
	req.Operation, req.PredecessorSHA256, req.TargetSHA256, req.LuaSHA256, req.RestoreRequestSHA256 = "cold-ordinary-rollback-plan", hostPhaseResultSHA(t, inspected), target.Native.B0TargetSHA256, hostDigest(mustHostForwardLua(t)), ""
	req.OrdinaryRequestSHA256 = retain(ordinary)
	ordinaryPlan := call(req)
	if ordinaryPlan.Native.OrdinaryRestorationMode != "legacy" {
		t.Fatal("original legacy ordinary mode lost")
	}
	req.Operation, req.PredecessorSHA256, req.OrdinaryRestorationPlanSHA256 = "cold-ordinary-rollback-retain", hostPhaseResultSHA(t, ordinaryPlan), ordinaryPlan.Native.OrdinaryRestorationPlanSHA256
	ordinaryRetained := call(req)
	req.Operation, req.PredecessorSHA256, req.TargetSHA256, req.LuaSHA256 = "cold-ordinary-rollback-inspect", hostPhaseResultSHA(t, ordinaryRetained), "", ""
	call(req)
	base := "lightpanda-b0:{host-selected-redis}:"
	for _, suffix := range []string{"route", "records", "ready", "inflight", "dead", "terminal", "origin-holders"} {
		if f.r.Exists(ctx, base+suffix).Val() != 0 {
			t.Fatal("candidate source queue survived restoration", suffix)
		}
	}
	owner, err := f.r.HGetAll(ctx, "lightpanda-b0:producer-owner").Result()
	if err != nil || len(owner) != 8 || owner["schema"] != "jobseek.lightpanda.producer-rollback/v1" || owner["routing_epoch"] != fmt.Sprint(active.Native.RoutingEpoch) || owner["rollback_plan_digest"] != req.B0RollbackPlanSHA256 || owner["source_receipt_sha256"] != restore.SourceReceiptSHA256 {
		t.Fatal("exact candidate N tombstone missing")
	}
	for _, p := range seed.Postings {
		prefix := "scrapes_browser:"
		if p.First {
			prefix = "ft_scrapes_browser:"
		}
		score, err := f.r.ZScore(ctx, prefix+"jobs.example.test", p.ID).Result()
		// Ready, unattempted source work retains its exact captured legacy score.
		want, parseErr := strconv.ParseFloat(p.Score, 64)
		if err != nil || parseErr != nil || score != want {
			t.Fatal("restoration changed unattempted score", err, score, p.Score)
		}
		config, err := f.r.HGetAll(ctx, "scrape:"+p.ID).Result()
		if err != nil || len(config) != 6 || config["board_id"] != seed.Board || config["description_r2_hash"] != "-9223372036854775808" || config["scrape_interval_hours"] != "24" {
			t.Fatal("canonical SQL hash/interval restoration lost")
		}
	}
	if f.r.Exists(ctx, "scrape:"+seed.Terminal).Val() != 0 || f.r.ZScore(ctx, "ft_scrapes_browser:jobs.example.test", seed.Terminal).Err() == nil || f.r.ZScore(ctx, "scrapes_browser:jobs.example.test", seed.Terminal).Err() == nil {
		t.Fatal("inactive terminal source work was requeued")
	}
	var fences int
	if f.pg.QueryRow(ctx, "SELECT count(*) FROM lightpanda_b0_write_fence WHERE shard_id='lightpanda-b0' AND routing_epoch=$1", active.Native.RoutingEpoch).Scan(&fences) != nil || fences != 0 {
		t.Fatal("source N SQL fences survived restoration")
	}
	after := fullColdExecutableRedisSnapshot(t, f)
	assertHostForwardReadiness(t, before, after, seed)
	allowed := map[string]bool{"lightpanda-b0:producer-owner": true, "lightpanda-b0:legacy-guard": true, "ft_scrapes_browser:jobs.example.test": true, "scrapes_browser:jobs.example.test": true}
	for _, suffix := range []string{"route", "records", "ready", "inflight", "dead", "terminal", "origin-holders"} {
		allowed[base+suffix] = true
	}
	for _, id := range []string{seed.Postings[0].ID, seed.Postings[1].ID, seed.Terminal} {
		allowed["scrape:"+id] = true
	}
	for key, value := range before {
		if !allowed[key] && !hostForwardReadinessKey(key) && after[key] != value {
			t.Fatal("restoration changed unrelated value/type/expiry", key)
		}
	}
	for key, value := range after {
		if !allowed[key] && !hostForwardReadinessKey(key) && before[key] != value {
			t.Fatal("restoration introduced unrelated value/type/expiry", key)
		}
	}
	got, err := os.ReadFile(sentinelPath)
	if err != nil || !reflect.DeepEqual(got, sentinel) || CheckHostMutationScope(ctx) != nil {
		t.Fatal("restoration implicitly cleared candidate sentinel or original lock")
	}
	return &hostForwardRestorationProof{req.RetirementEpoch, req.ReversalSHA256, req.B0RollbackPlanSHA256, req.OrdinaryRestorationPlanSHA256, restore.SourceReceiptSHA256, after, hostDigest(sentinel)}
}

func mustHostForwardLua(t *testing.T) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "..", "src", "lua", "lightpanda_b0_queue.lua"))
	if err != nil {
		t.Fatal(err)
	}
	return b
}
