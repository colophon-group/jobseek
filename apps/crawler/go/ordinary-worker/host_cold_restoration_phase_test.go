package worker

import (
	"encoding/json"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func hostRestorationTestRequest(operation string) HostColdPhaseRequest {
	hash := strings.Repeat("a", 64)
	r := HostColdPhaseRequest{Version: "jobseek.crawler-host-cold-request/v2", Binding: queue.HostColdSQLBinding{SourceRevision: strings.Repeat("b", 40), RequestSHA256: hash, ContainmentIntentSHA256: hash}, RedisEndpointSHA256: hash, RedisInstanceSHA256: hash, Operation: operation, PredecessorSHA256: hash, PreviousEpoch: 1, RoutingEpoch: 2, RetirementEpoch: 3, IntentSHA256: hash, PlanSHA256: hash, ReversalSHA256: hash, TargetSHA256: hash, LuaSHA256: hash, B0RollbackPlanSHA256: hash}
	if coldB0RestorationOperation(operation) {
		r.RestoreRequestSHA256 = hash
		if operation == "cold-b0-rollback-plan" {
			r.B0RollbackPlanSHA256 = ""
		}
	} else {
		r.OrdinaryRequestSHA256 = hash
		if operation != "cold-ordinary-rollback-plan" {
			r.OrdinaryRestorationPlanSHA256 = hash
		}
	}
	if strings.HasSuffix(operation, "-inspect") {
		r.TargetSHA256, r.LuaSHA256 = "", ""
	}
	return r
}

var hostRestorationTestOperations = []string{"cold-b0-rollback-plan", "cold-b0-rollback-retain", "cold-b0-rollback-restore", "cold-b0-rollback-inspect", "cold-ordinary-rollback-plan", "cold-ordinary-rollback-retain", "cold-ordinary-rollback-inspect"}

func TestHostColdRestorationRequestsRejectCrossStageAuthority(t *testing.T) {
	decode := func(r HostColdPhaseRequest) error {
		body, _ := json.Marshal(r)
		_, err := decodeHostColdPhase(body, hostDigest(body), r.Binding)
		return err
	}
	for _, operation := range hostRestorationTestOperations {
		t.Run(operation, func(t *testing.T) {
			r := hostRestorationTestRequest(operation)
			if decode(r) != nil {
				t.Fatal("closed restoration refused")
			}
			for name, mutate := range map[string]func(*HostColdPhaseRequest){
				"missing parent":        func(r *HostColdPhaseRequest) { r.PredecessorSHA256 = "" },
				"missing intent":        func(r *HostColdPhaseRequest) { r.IntentSHA256 = "" },
				"missing reversal":      func(r *HostColdPhaseRequest) { r.ReversalSHA256 = "" },
				"missing original plan": func(r *HostColdPhaseRequest) { r.PlanSHA256 = "" },
				"old retirement":        func(r *HostColdPhaseRequest) { r.RetirementEpoch = r.RoutingEpoch },
				"overflow retirement":   func(r *HostColdPhaseRequest) { r.RetirementEpoch = 10000000000000 },
				"forward request":       func(r *HostColdPhaseRequest) { r.ForwardRequestSHA256 = strings.Repeat("c", 64) },
				"forward receipt":       func(r *HostColdPhaseRequest) { r.ForwardReceiptSHA256 = strings.Repeat("c", 64) },
				"caller namespace":      func(r *HostColdPhaseRequest) { r.Namespace = "substitute" },
				"cross-stage request": func(r *HostColdPhaseRequest) {
					if coldB0RestorationOperation(r.Operation) {
						r.OrdinaryRequestSHA256 = strings.Repeat("c", 64)
					} else {
						r.RestoreRequestSHA256 = strings.Repeat("c", 64)
					}
				},
				"wrong target inputs": func(r *HostColdPhaseRequest) {
					if strings.HasSuffix(r.Operation, "-inspect") {
						r.TargetSHA256 = strings.Repeat("c", 64)
					} else {
						r.TargetSHA256 = ""
					}
				},
			} {
				t.Run(name, func(t *testing.T) {
					bad := r
					mutate(&bad)
					if decode(bad) == nil {
						t.Fatal("ambiguous restoration admitted")
					}
				})
			}
		})
	}
	for _, operation := range []string{"cold-inspect", "cold-reversal-begin", "cold-reversal-reserve", "cold-reversal-inspect"} {
		r := hostRestorationTestRequest("cold-b0-rollback-plan")
		r.Operation = operation
		r.TargetSHA256, r.LuaSHA256, r.RetirementEpoch = "", "", 0
		if operation == "cold-reversal-inspect" {
			r.RetirementEpoch = 3
		}
		if decode(r) == nil {
			t.Fatal("restoration input admitted outside restoration", operation)
		}
	}
}

func TestHostColdRestorationLinksRejectSkippedOrSubstitutedHistory(t *testing.T) {
	for _, operation := range hostRestorationTestOperations {
		t.Run(operation, func(t *testing.T) {
			r := hostRestorationTestRequest(operation)
			p := r
			p.Operation = hostColdRestorationPrior(operation)
			n := &ColdAdminIdentity{Version: "jobseek.ordinary.cold-identity/v1", Operation: p.Operation, SourceRevision: r.Binding.SourceRevision, IntentSHA256: r.IntentSHA256, ReversalSHA256: r.ReversalSHA256, RoutingEpoch: r.RoutingEpoch, RetirementEpoch: r.RetirementEpoch, PlanSHA256: r.PlanSHA256, B0RollbackPlanSHA256: r.B0RollbackPlanSHA256, ReversalPhase: "reserved", B0RestorationPhase: "fences-cleared"}
			if operation == "cold-b0-rollback-restore" {
				n.B0RestorationPhase = "prepared"
			}
			if coldOrdinaryRestorationOperation(operation) && operation != "cold-ordinary-rollback-plan" {
				n.OrdinaryRestorationPlan = json.RawMessage(`{"fixture":"exact"}`)
				r.OrdinaryRestorationPlanSHA256 = hostDigest(n.OrdinaryRestorationPlan)
				p.OrdinaryRestorationPlanSHA256, n.OrdinaryRestorationPlanSHA256 = r.OrdinaryRestorationPlanSHA256, r.OrdinaryRestorationPlanSHA256
			}
			parent := &HostColdPhaseResult{Outcome: "completed", Native: n}
			if checkHostColdRestorationLink(r, p, parent) != nil {
				t.Fatal("exact restoration link refused")
			}
			for _, fault := range []string{"unresolved", "skipped", "epoch", "retirement", "reversal", "plan", "source", "request"} {
				badRequest, badParent, native, prior := r, *parent, *n, p
				badParent.Native = &native
				switch fault {
				case "unresolved":
					badParent.Outcome = "unresolved"
				case "skipped":
					prior.Operation = "cold-inspect"
				case "epoch":
					native.RoutingEpoch++
				case "retirement":
					native.RetirementEpoch++
				case "reversal":
					native.ReversalSHA256 = strings.Repeat("c", 64)
				case "plan":
					native.PlanSHA256 = strings.Repeat("c", 64)
				case "source":
					native.SourceRevision = strings.Repeat("c", 40)
				case "request":
					if operation == "cold-b0-rollback-plan" || operation == "cold-ordinary-rollback-plan" {
						continue
					}
					if coldB0RestorationOperation(operation) {
						badRequest.RestoreRequestSHA256 = strings.Repeat("c", 64)
					} else {
						badRequest.OrdinaryRequestSHA256 = strings.Repeat("c", 64)
					}
				}
				if checkHostColdRestorationLink(badRequest, prior, &badParent) == nil {
					t.Fatal("altered restoration ancestry admitted", fault)
				}
			}
		})
	}
}

func TestHostColdRestorationResultsBindCanonicalRequestAndPlan(t *testing.T) {
	r := hostRestorationTestRequest("cold-b0-rollback-plan")
	request := queue.ColdB0RollbackRequest{ReversalSHA256: r.ReversalSHA256, SourceRevision: r.Binding.SourceRevision, RetirementEpoch: r.RetirementEpoch, B0SourceEpoch: r.PreviousEpoch, SourceReceiptSHA256: strings.Repeat("c", 64)}
	body, _ := json.Marshal(request)
	r.RestoreRequestSHA256 = hostDigest(body)
	inputs := map[string][]byte{"cold-input-" + r.RestoreRequestSHA256: body}
	plan, _ := json.Marshal(struct {
		Request queue.ColdB0RollbackRequest `json:"request"`
		Target  string                      `json:"target_sha256"`
	}{request, r.TargetSHA256})
	n := &ColdAdminIdentity{IntentSHA256: r.IntentSHA256, RoutingEpoch: r.RoutingEpoch, PlanSHA256: r.PlanSHA256, ReversalSHA256: r.ReversalSHA256, RetirementEpoch: r.RetirementEpoch, B0TargetSHA256: r.TargetSHA256, B0RollbackPlanSHA256: hostDigest(plan), B0RollbackPlan: plan}
	if validateHostColdRestorationResult(r, n, inputs, nil) != nil {
		t.Fatal("exact native preview refused")
	}
	for _, fault := range []string{"retirement", "target", "digest", "request"} {
		bad := *n
		switch fault {
		case "retirement":
			bad.RetirementEpoch++
		case "target":
			bad.B0TargetSHA256 = strings.Repeat("d", 64)
		case "digest":
			bad.B0RollbackPlanSHA256 = strings.Repeat("d", 64)
		case "request":
			altered := request
			altered.SourceReceiptSHA256 = strings.Repeat("d", 64)
			bad.B0RollbackPlan, _ = json.Marshal(struct {
				Request queue.ColdB0RollbackRequest `json:"request"`
				Target  string                      `json:"target_sha256"`
			}{altered, r.TargetSHA256})
			bad.B0RollbackPlanSHA256 = hostDigest(bad.B0RollbackPlan)
		}
		if validateHostColdRestorationResult(r, &bad, inputs, nil) == nil {
			t.Fatal("altered result admitted", fault)
		}
	}
}
