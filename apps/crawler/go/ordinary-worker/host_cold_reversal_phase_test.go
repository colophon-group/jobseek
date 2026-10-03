package worker

import (
	"encoding/json"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestHostColdReversalRequestsRejectAmbiguousAuthority(t *testing.T) {
	hash := strings.Repeat("a", 64)
	binding := queue.HostColdSQLBinding{SourceRevision: strings.Repeat("b", 40), RequestSHA256: hash, ContainmentIntentSHA256: hash}
	base := HostColdPhaseRequest{Version: "jobseek.crawler-host-cold-request/v2", Binding: binding, RedisEndpointSHA256: hash, RedisInstanceSHA256: hash, PredecessorSHA256: hash, PreviousEpoch: 1, RoutingEpoch: 2, IntentSHA256: hash, PlanSHA256: hash, ReversalSHA256: hash}
	decode := func(r HostColdPhaseRequest) error {
		body, _ := json.Marshal(r)
		_, err := decodeHostColdPhase(body, hostDigest(body), binding)
		return err
	}
	for _, operation := range []string{"cold-reversal-begin", "cold-reversal-reserve", "cold-reversal-inspect"} {
		t.Run(operation, func(t *testing.T) {
			r := base
			r.Operation = operation
			if operation == "cold-reversal-inspect" {
				r.RetirementEpoch = 3
			}
			if decode(r) != nil {
				t.Fatal("explicit reversal refused")
			}
			for name, mutate := range map[string]func(*HostColdPhaseRequest){
				"missing predecessor": func(r *HostColdPhaseRequest) { r.PredecessorSHA256 = "" },
				"missing reversal":    func(r *HostColdPhaseRequest) { r.ReversalSHA256 = "" },
				"missing plan":        func(r *HostColdPhaseRequest) { r.PlanSHA256 = "" },
				"missing intent":      func(r *HostColdPhaseRequest) { r.IntentSHA256 = "" },
				"old epoch":           func(r *HostColdPhaseRequest) { r.RoutingEpoch = r.PreviousEpoch },
				"overflow":            func(r *HostColdPhaseRequest) { r.RoutingEpoch = 9999999999999 },
				"forward request":     func(r *HostColdPhaseRequest) { r.ForwardRequestSHA256 = hash },
				"forward plan":        func(r *HostColdPhaseRequest) { r.ForwardPlanSHA256 = hash },
				"forward receipt":     func(r *HostColdPhaseRequest) { r.ForwardReceiptSHA256 = hash },
				"target":              func(r *HostColdPhaseRequest) { r.TargetSHA256 = hash },
				"Lua":                 func(r *HostColdPhaseRequest) { r.LuaSHA256 = hash },
				"caller cohort":       func(r *HostColdPhaseRequest) { r.Cohort = "c1" },
				"wrong retirement":    func(r *HostColdPhaseRequest) { r.RetirementEpoch = r.RoutingEpoch },
			} {
				t.Run(name, func(t *testing.T) {
					bad := r
					mutate(&bad)
					if decode(bad) == nil {
						t.Fatal("ambiguous reversal admitted")
					}
				})
			}
		})
	}
	for _, operation := range []string{"cold-inspect", "cold-b0-forward-plan"} {
		r := base
		r.Operation = operation
		if operation == "cold-b0-forward-plan" {
			r.ForwardRequestSHA256, r.TargetSHA256, r.LuaSHA256 = hash, hash, hash
		} else {
			r.RoutingEpoch, r.PlanSHA256 = 0, ""
		}
		if decode(r) == nil {
			t.Fatal("cross-stage reversal inputs admitted")
		}
	}
}

func TestHostColdReversalLinksBindForwardPhaseAndRetirement(t *testing.T) {
	hash := strings.Repeat("a", 64)
	r := HostColdPhaseRequest{Operation: "cold-reversal-begin", Binding: queue.HostColdSQLBinding{SourceRevision: strings.Repeat("b", 40)}, PredecessorSHA256: hash, PreviousEpoch: 1, RoutingEpoch: 2, IntentSHA256: hash, PlanSHA256: hash, ReversalSHA256: hash}
	for _, operation := range []string{"cold-reserve", "cold-inspect", "cold-b0-forward-plan", "cold-b0-forward-retain", "cold-b0-forward-apply", "cold-b0-forward-inspect", "cold-forward-prepare", "cold-forward-publish", "cold-forward-activate"} {
		p := r
		p.Operation, p.ReversalSHA256 = operation, ""
		n := &ColdAdminIdentity{Version: "jobseek.ordinary.cold-identity/v1", Operation: operation, SourceRevision: r.Binding.SourceRevision, IntentSHA256: hash, RoutingEpoch: 2, PlanSHA256: hash, RetainedPhase: "reserved"}
		parent := &HostColdPhaseResult{Outcome: "completed", Native: n}
		if checkHostColdReversalLink(r, p, parent) != nil {
			t.Fatal("completed forward anchor refused", operation)
		}
		if coldB0ForwardOperation(operation) {
			bad := p
			bad.RoutingEpoch++
			if checkHostColdReversalLink(r, bad, parent) == nil {
				t.Fatal("inconsistent retained forward request epoch admitted", operation)
			}
			bad = p
			bad.PlanSHA256 = strings.Repeat("c", 64)
			if checkHostColdReversalLink(r, bad, parent) == nil {
				t.Fatal("inconsistent retained forward request plan admitted", operation)
			}
		}
		for _, fault := range []string{"unresolved", "epoch", "plan", "intent", "source", "wrong inspection phase"} {
			bad, native := *parent, *n
			bad.Native = &native
			switch fault {
			case "unresolved":
				bad.Outcome, bad.Native = "unresolved", nil
			case "epoch":
				native.RoutingEpoch++
			case "plan":
				native.PlanSHA256 = strings.Repeat("c", 64)
			case "intent":
				native.IntentSHA256 = strings.Repeat("c", 64)
			case "source":
				native.SourceRevision = strings.Repeat("c", 40)
			case "wrong inspection phase":
				if operation != "cold-inspect" {
					continue
				}
				native.RetainedPhase = "active"
			}
			if checkHostColdReversalLink(r, p, &bad) == nil {
				t.Fatal("substituted forward anchor admitted", operation, fault)
			}
		}
	}
	p := r
	p.Operation = "cold-reversal-begin"
	n := &ColdAdminIdentity{Version: "jobseek.ordinary.cold-identity/v1", Operation: p.Operation, SourceRevision: r.Binding.SourceRevision, IntentSHA256: hash, RoutingEpoch: 2, PlanSHA256: hash, ReversalSHA256: hash}
	parent := &HostColdPhaseResult{Outcome: "completed", Native: n}
	r.Operation = "cold-reversal-reserve"
	if checkHostColdReversalLink(r, p, parent) != nil {
		t.Fatal("exact reversal reserve refused")
	}
	p = r
	n.Operation, n.ReversalPhase, n.RetirementEpoch = p.Operation, "reserved", 3
	r.Operation, r.RetirementEpoch = "cold-reversal-inspect", 3
	if checkHostColdReversalLink(r, p, parent) != nil {
		t.Fatal("exact retirement inspection refused")
	}
	r.RetirementEpoch++
	if checkHostColdReversalLink(r, p, parent) == nil {
		t.Fatal("adopted another retirement epoch")
	}
}
