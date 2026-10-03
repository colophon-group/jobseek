package worker

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestHostColdForwardRequestsRefuseImplicitAndCrossStageInputs(t *testing.T) {
	hash := strings.Repeat("a", 64)
	binding := queue.HostColdSQLBinding{SourceRevision: strings.Repeat("b", 40), RequestSHA256: hash, ContainmentIntentSHA256: hash}
	base := HostColdPhaseRequest{Version: "jobseek.crawler-host-cold-request/v2", Binding: binding, RedisEndpointSHA256: hash, RedisInstanceSHA256: hash, PredecessorSHA256: hash, PreviousEpoch: 1, RoutingEpoch: 2, IntentSHA256: hash, PlanSHA256: hash, ForwardRequestSHA256: hash, TargetSHA256: hash, LuaSHA256: hash}
	decode := func(r HostColdPhaseRequest) error {
		body, _ := json.Marshal(r)
		_, err := decodeHostColdPhase(body, hostDigest(body), binding)
		return err
	}
	for _, operation := range []string{"cold-b0-forward-plan", "cold-b0-forward-retain", "cold-b0-forward-apply", "cold-b0-forward-inspect", "cold-forward-prepare", "cold-forward-publish", "cold-forward-activate"} {
		t.Run(operation, func(t *testing.T) {
			r := base
			r.Operation = operation
			if operation != "cold-b0-forward-plan" {
				r.ForwardPlanSHA256 = hash
			}
			if operation == "cold-b0-forward-inspect" {
				r.TargetSHA256, r.LuaSHA256 = "", ""
			}
			if coldForwardPublicationOperation(operation) {
				r.ForwardReceiptSHA256 = hash
			}
			if decode(r) != nil {
				t.Fatal("explicit request refused")
			}
			for name, mutate := range map[string]func(*HostColdPhaseRequest){
				"implicit epoch":                    func(r *HostColdPhaseRequest) { r.RoutingEpoch = 0 },
				"old epoch":                         func(r *HostColdPhaseRequest) { r.RoutingEpoch = r.PreviousEpoch },
				"allocator overflow":                func(r *HostColdPhaseRequest) { r.RoutingEpoch = 10000000000000 },
				"implicit ordinary plan":            func(r *HostColdPhaseRequest) { r.PlanSHA256 = "" },
				"missing producer transfer request": func(r *HostColdPhaseRequest) { r.ForwardRequestSHA256 = "" },
				"missing predecessor":               func(r *HostColdPhaseRequest) { r.PredecessorSHA256 = "" },
				"caller cohort":                     func(r *HostColdPhaseRequest) { r.Cohort = "alternate" },
				"ordinary-only shortcut":            func(r *HostColdPhaseRequest) { r.Operation = "cold-publish" },
			} {
				t.Run(name, func(t *testing.T) {
					bad := r
					mutate(&bad)
					if decode(bad) == nil {
						t.Fatal("ambiguous authority admitted")
					}
				})
			}
			bad := r
			if coldForwardPublicationOperation(operation) {
				bad.ForwardReceiptSHA256 = ""
			} else {
				bad.ForwardReceiptSHA256 = hash
			}
			if decode(bad) == nil {
				t.Fatal("cross-stage receipt admitted")
			}
		})
	}
	base.Operation, base.TargetSHA256, base.LuaSHA256 = "cold-inspect", "", ""
	if decode(base) == nil {
		t.Fatal("initial inspection accepted forward-only fields")
	}
}

func TestHostColdForwardLinksRejectSkippedAndSubstitutedTransfer(t *testing.T) {
	plan := []byte(`{"version":"private-pure-link-fixture"}`)
	hash, planHash := strings.Repeat("a", 64), hostDigest(plan)
	r := HostColdPhaseRequest{Operation: "cold-forward-prepare", Binding: queue.HostColdSQLBinding{SourceRevision: strings.Repeat("b", 40)}, PredecessorSHA256: hash, PreviousEpoch: 1, RoutingEpoch: 2, IntentSHA256: hash, PlanSHA256: hash, ForwardRequestSHA256: hash, ForwardPlanSHA256: planHash, ForwardReceiptSHA256: hash, TargetSHA256: hash, LuaSHA256: hash}
	p := r
	p.Operation, p.TargetSHA256, p.LuaSHA256, p.ForwardReceiptSHA256 = "cold-b0-forward-inspect", "", "", ""
	n := &ColdAdminIdentity{Version: "jobseek.ordinary.cold-identity/v1", Operation: p.Operation, SourceRevision: r.Binding.SourceRevision, IntentSHA256: hash, RoutingEpoch: 2, PlanSHA256: hash, B0ForwardPlanSHA256: planHash, B0ForwardPlan: plan, B0ForwardPhase: "redis-transferred", B0ForwardReceiptSHA256: hash}
	parent := &HostColdPhaseResult{Outcome: "completed", Native: n}
	if checkHostColdForwardLink(r, p, parent) != nil {
		t.Fatal("explicit pure link refused")
	}
	for _, fault := range []string{"skipped inspection", "different request", "different epoch", "different plan", "different receipt", "pending transfer", "changed plan bytes", "unresolved"} {
		t.Run(fault, func(t *testing.T) {
			prior, native, result := p, *n, *parent
			result.Native = &native
			switch fault {
			case "skipped inspection":
				prior.Operation = "cold-b0-forward-apply"
			case "different request":
				prior.ForwardRequestSHA256 = strings.Repeat("c", 64)
			case "different epoch":
				native.RoutingEpoch++
			case "different plan":
				native.PlanSHA256 = strings.Repeat("c", 64)
			case "different receipt":
				native.B0ForwardReceiptSHA256 = strings.Repeat("c", 64)
			case "pending transfer":
				native.B0ForwardPhase = "prepared"
			case "changed plan bytes":
				native.B0ForwardPlan = []byte(`{}`)
			case "unresolved":
				result.Outcome, result.Native = "unresolved", nil
			}
			if checkHostColdForwardLink(r, prior, &result) == nil {
				t.Fatal("substituted successor authority admitted")
			}
		})
	}
}

func TestHostJournalRetainsBoundedLargePlansWithExactCrashRetry(t *testing.T) {
	s, err := openHostStore(hostPrivateDirectory(t))
	if err != nil {
		t.Fatal("private store")
	}
	defer s.Close()
	body := bytes.Repeat([]byte("x"), (8<<20)+1)
	if s.retain("plan", body, nil) == nil {
		t.Fatal("general evidence ceiling changed")
	}
	if s.retainLimit("plan", body, func(stage string) error {
		if stage == "file_linked" {
			return errHostPreflight
		}
		return nil
	}, 32<<20) == nil {
		t.Fatal("publication seam did not interrupt")
	}
	if s.retainLimit("plan", body, nil, 32<<20) != nil {
		t.Fatal("large exact retry refused")
	}
	retained, err := s.readLimit("plan", false, 32<<20)
	if err != nil || !bytes.Equal(retained, body) {
		t.Fatal("large retained bytes changed")
	}
	body[0] = 'y'
	if s.retainLimit("plan", body, nil, 32<<20) == nil || s.retainLimit("overflow", body, nil, 48<<20+1) == nil {
		t.Fatal("replacement or unbounded ceiling accepted")
	}
}
