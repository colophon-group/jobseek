package worker

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

var hostCompletionTestOperations = []string{"cold-b0-reactivation-plan", "cold-b0-reactivation-retain", "cold-b0-reactivation-apply", "cold-b0-reactivation-inspect", "cold-ordinary-finalization-plan", "cold-ordinary-finalization-retain", "cold-ordinary-finalization-prepare", "cold-ordinary-finalization-publish", "cold-ordinary-finalization-complete", "cold-ordinary-finalization-inspect"}

func hostCompletionTestRequest(operation string) HostColdPhaseRequest {
	hash := strings.Repeat("a", 64)
	r := hostRestorationTestRequest("cold-ordinary-rollback-retain")
	r.Operation, r.B0RollbackPlanSHA256, r.OrdinaryRequestSHA256 = operation, "", ""
	if coldB0ReactivationOperation(operation) {
		r.PriorB0ReceiptSHA256 = hash
		if operation != "cold-b0-reactivation-plan" {
			r.B0ReactivationPlanSHA256 = hash
		}
		if operation == "cold-b0-reactivation-inspect" {
			r.PriorB0ReceiptSHA256 = ""
		}
	} else {
		r.B0ReactivationPlanSHA256, r.B0ReactivationReceiptSHA256, r.FinalizationRequestSHA256 = hash, hash, hash
		if operation != "cold-ordinary-finalization-plan" {
			r.FinalizationPlanSHA256 = hash
		}
	}
	if strings.HasSuffix(operation, "-inspect") {
		r.TargetSHA256, r.LuaSHA256 = "", ""
	}
	return r
}

func TestHostColdCompletionRequestsRefuseImplicitAndCrossStageInputs(t *testing.T) {
	decode := func(r HostColdPhaseRequest) error {
		b, _ := json.Marshal(r)
		_, err := decodeHostColdPhase(b, hostDigest(b), r.Binding)
		return err
	}
	for _, operation := range hostCompletionTestOperations {
		t.Run(operation, func(t *testing.T) {
			r := hostCompletionTestRequest(operation)
			if decode(r) != nil {
				t.Fatal("closed completion request refused")
			}
			for fault, mutate := range map[string]func(*HostColdPhaseRequest){
				"no parent":               func(r *HostColdPhaseRequest) { r.PredecessorSHA256 = "" },
				"no original plan":        func(r *HostColdPhaseRequest) { r.PlanSHA256 = "" },
				"no ordinary restoration": func(r *HostColdPhaseRequest) { r.OrdinaryRestorationPlanSHA256 = "" },
				"old retirement":          func(r *HostColdPhaseRequest) { r.RetirementEpoch = r.RoutingEpoch },
				"overflow":                func(r *HostColdPhaseRequest) { r.RetirementEpoch = 10000000000000 },
				"restore request":         func(r *HostColdPhaseRequest) { r.RestoreRequestSHA256 = strings.Repeat("c", 64) },
				"restore plan":            func(r *HostColdPhaseRequest) { r.B0RollbackPlanSHA256 = strings.Repeat("c", 64) },
				"forward receipt":         func(r *HostColdPhaseRequest) { r.ForwardReceiptSHA256 = strings.Repeat("c", 64) },
				"caller namespace":        func(r *HostColdPhaseRequest) { r.Namespace = "substitute" },
				"wrong target": func(r *HostColdPhaseRequest) {
					if strings.HasSuffix(r.Operation, "-inspect") {
						r.TargetSHA256 = strings.Repeat("c", 64)
					} else {
						r.TargetSHA256 = ""
					}
				},
				"cross-stage receipt": func(r *HostColdPhaseRequest) {
					if coldB0ReactivationOperation(r.Operation) {
						r.B0ReactivationReceiptSHA256 = strings.Repeat("c", 64)
					} else {
						r.PriorB0ReceiptSHA256 = strings.Repeat("c", 64)
					}
				},
			} {
				t.Run(fault, func(t *testing.T) {
					bad := r
					mutate(&bad)
					if decode(bad) == nil {
						t.Fatal("ambiguous completion admitted")
					}
				})
			}
		})
	}
	for _, operation := range hostRestorationTestOperations {
		r := hostRestorationTestRequest(operation)
		r.B0ReactivationPlanSHA256 = strings.Repeat("c", 64)
		if decode(r) == nil {
			t.Fatal("completion field admitted in restoration", operation)
		}
	}
}

func TestHostColdCompletionLinksBindAllTenPredecessorsAndRetirement(t *testing.T) {
	for _, operation := range hostCompletionTestOperations {
		t.Run(operation, func(t *testing.T) {
			r := hostCompletionTestRequest(operation)
			p := r
			p.Operation = hostColdCompletionPrior(operation)
			n := &ColdAdminIdentity{Version: "jobseek.ordinary.cold-identity/v1", Operation: p.Operation, SourceRevision: r.Binding.SourceRevision, IntentSHA256: r.IntentSHA256, RoutingEpoch: r.RoutingEpoch, PlanSHA256: r.PlanSHA256, ReversalSHA256: r.ReversalSHA256, RetirementEpoch: r.RetirementEpoch, OrdinaryRestorationPlanSHA256: r.OrdinaryRestorationPlanSHA256, B0ReactivationPlanSHA256: r.B0ReactivationPlanSHA256, B0ReactivationReceiptSHA256: r.B0ReactivationReceiptSHA256, OrdinaryFinalizationPhase: hostColdFinalizationPriorPhase(operation)}
			if operation == "cold-b0-reactivation-plan" {
				n.OrdinaryRestorationPlan = json.RawMessage(`{"exact":"ordinary"}`)
				r.OrdinaryRestorationPlanSHA256 = hostDigest(n.OrdinaryRestorationPlan)
				p.OrdinaryRestorationPlanSHA256, n.OrdinaryRestorationPlanSHA256 = r.OrdinaryRestorationPlanSHA256, r.OrdinaryRestorationPlanSHA256
			}
			if coldB0ReactivationOperation(operation) && operation != "cold-b0-reactivation-plan" {
				n.B0ReactivationPlan = json.RawMessage(`{"exact":"reactivation"}`)
				r.B0ReactivationPlanSHA256 = hostDigest(n.B0ReactivationPlan)
				p.B0ReactivationPlanSHA256, n.B0ReactivationPlanSHA256 = r.B0ReactivationPlanSHA256, r.B0ReactivationPlanSHA256
			}
			if operation == "cold-b0-reactivation-inspect" || operation == "cold-ordinary-finalization-plan" {
				n.B0ReactivationPhase = "redis-reactivated"
				n.B0ReactivationReceiptSHA256 = strings.Repeat("a", 64)
			}
			if coldOrdinaryFinalizationOperation(operation) && operation != "cold-ordinary-finalization-plan" {
				n.OrdinaryFinalizationPlan = json.RawMessage(`{"exact":"finalization"}`)
				r.FinalizationPlanSHA256 = hostDigest(n.OrdinaryFinalizationPlan)
				p.FinalizationPlanSHA256, n.OrdinaryFinalizationPlanSHA256 = r.FinalizationPlanSHA256, r.FinalizationPlanSHA256
			}
			parent := &HostColdPhaseResult{Outcome: "completed", Native: n}
			if checkHostColdCompletionLink(r, p, parent) != nil {
				t.Fatal("exact completed predecessor refused")
			}
			for _, fault := range []string{"unresolved", "skipped", "source", "N", "R", "reversal", "ordinary plan", "original plan", "phase"} {
				bad, native, prior := *parent, *n, p
				bad.Native = &native
				switch fault {
				case "unresolved":
					bad.Outcome = "unresolved"
				case "skipped":
					prior.Operation = "cold-reversal-inspect"
				case "source":
					native.SourceRevision = strings.Repeat("c", 40)
				case "N":
					native.RoutingEpoch++
				case "R":
					native.RetirementEpoch++
				case "reversal":
					native.ReversalSHA256 = strings.Repeat("c", 64)
				case "ordinary plan":
					native.OrdinaryRestorationPlanSHA256 = strings.Repeat("c", 64)
				case "original plan":
					native.PlanSHA256 = strings.Repeat("c", 64)
				case "phase":
					if coldB0ReactivationOperation(operation) && operation != "cold-b0-reactivation-plan" || operation == "cold-ordinary-finalization-plan" {
						native.B0ReactivationPhase = "wrong"
					} else if coldOrdinaryFinalizationOperation(operation) {
						native.OrdinaryFinalizationPhase = "wrong"
					} else {
						continue
					}
				}
				if checkHostColdCompletionLink(r, prior, &bad) == nil {
					t.Fatal("substituted completed history admitted", fault)
				}
			}
		})
	}
}

func TestHostColdCompletionInputsBindOriginalReceiptAndLiveColdEvidence(t *testing.T) {
	r := hostCompletionTestRequest("cold-b0-reactivation-plan")
	body := []byte("fixture historical receipt\n")
	r.PriorB0ReceiptSHA256 = hostDigest(body)
	s := &hostColdPhaseScope{info: HostColdPhaseContext{ActiveReleaseSHA256: strings.Repeat("d", 64), ColdAttestationSHA256: strings.Repeat("e", 64)}}
	intent := queue.ColdTransitionSpec{PreviousB0ReceiptSHA256: r.PriorB0ReceiptSHA256, TransitionID: "00000000-0000-4000-8000-000000000001"}
	inputs := map[string][]byte{"cold-input-" + r.PriorB0ReceiptSHA256: body}
	if s.checkCompletionInputs(r, intent, inputs) != nil {
		t.Fatal("original retained receipt refused")
	}
	bad := intent
	bad.PreviousB0ReceiptSHA256 = strings.Repeat("c", 64)
	if s.checkCompletionInputs(r, bad, inputs) == nil {
		t.Fatal("substituted prior receipt admitted")
	}
	r = hostCompletionTestRequest("cold-ordinary-finalization-plan")
	request := queue.ColdOrdinaryFinalizationRequest{OrdinaryRestorationPlanSHA256: r.OrdinaryRestorationPlanSHA256, B0ReactivationPlanSHA256: r.B0ReactivationPlanSHA256, SourceRevision: r.Binding.SourceRevision, TransitionID: "00000000-0000-4000-8000-000000000002", ActiveReleaseSHA256: s.info.ActiveReleaseSHA256, ColdAttestationSHA256: s.info.ColdAttestationSHA256}
	for _, fault := range []string{"none", "source", "ordinary plan", "reactivation plan", "active release", "cold", "reused transition"} {
		bad := request
		switch fault {
		case "source":
			bad.SourceRevision = strings.Repeat("c", 40)
		case "ordinary plan":
			bad.OrdinaryRestorationPlanSHA256 = strings.Repeat("c", 64)
		case "reactivation plan":
			bad.B0ReactivationPlanSHA256 = strings.Repeat("c", 64)
		case "active release":
			bad.ActiveReleaseSHA256 = strings.Repeat("c", 64)
		case "cold":
			bad.ColdAttestationSHA256 = strings.Repeat("c", 64)
		case "reused transition":
			bad.TransitionID = intent.TransitionID
		}
		body, _ := json.Marshal(bad)
		r.FinalizationRequestSHA256 = hostDigest(body)
		inputs = map[string][]byte{"cold-input-" + r.FinalizationRequestSHA256: body}
		if (s.checkCompletionInputs(r, intent, inputs) == nil) != (fault == "none") {
			t.Fatal("finalization host evidence binding", fault)
		}
	}
}

func TestHostColdReactivationLargePlanRetentionRecoversExactLink(t *testing.T) {
	s, err := openHostStore(hostPrivateDirectory(t))
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	body := bytes.Repeat([]byte("x"), 56<<20)
	if s.retainLimit("general", body, nil, 64<<20) == nil {
		t.Fatal("general evidence acquired cold-only ceiling")
	}
	interrupted := false
	if s.retainColdPhaseLimit("reactivation", body, func(phase string) error {
		if phase == "file_linked" {
			interrupted = true
			return errHostPreflight
		}
		return nil
	}, 64<<20) == nil || !interrupted {
		t.Fatal("large plan link seam did not interrupt")
	}
	if s.retainColdPhaseLimit("reactivation", body, nil, 64<<20) != nil {
		t.Fatal("exact large plan retry failed")
	}
	got, err := s.readColdPhaseLimit("reactivation", false, 64<<20)
	if err != nil || !bytes.Equal(got, body) {
		t.Fatal("large native plan bytes changed")
	}
	if _, err := s.readLimit("reactivation", false, 64<<20); err == nil {
		t.Fatal("general evidence reader exceeded unchanged cap")
	}
	if s.retainColdPhaseLimit("overflow", []byte("x"), nil, (96<<20)+1) == nil {
		t.Fatal("unbounded result ceiling admitted")
	}
}

func TestHostColdCompletionResultsRefuseRehashedForeignReceipts(t *testing.T) {
	r := hostCompletionTestRequest("cold-ordinary-finalization-complete")
	request := queue.ColdOrdinaryFinalizationRequest{OrdinaryRestorationPlanSHA256: r.OrdinaryRestorationPlanSHA256, B0ReactivationPlanSHA256: r.B0ReactivationPlanSHA256, SourceRevision: r.Binding.SourceRevision, TransitionID: "00000000-0000-4000-8000-000000000002", ActiveReleaseSHA256: strings.Repeat("d", 64), ColdAttestationSHA256: strings.Repeat("e", 64)}
	body, _ := json.Marshal(request)
	r.FinalizationRequestSHA256 = hostDigest(body)
	inputs := map[string][]byte{"cold-input-" + r.FinalizationRequestSHA256: body}
	plan, _ := json.Marshal(map[string]any{"version": "jobseek.crawler.cold-ordinary-finalization/v1", "request": request, "reversal_sha256": r.ReversalSHA256, "retirement_epoch": r.RetirementEpoch, "mode": "legacy", "b0_receipt_sha256": r.B0ReactivationReceiptSHA256, "target_sha256": r.TargetSHA256})
	r.FinalizationPlanSHA256 = hostDigest(plan)
	publication := strings.Repeat("f", 64)
	receipt := map[string]any{"version": "jobseek.crawler.cold-ordinary-completion/v1", "plan_sha256": r.FinalizationPlanSHA256, "source_revision": r.Binding.SourceRevision, "retirement_epoch": r.RetirementEpoch, "publication_receipt_sha256": publication, "b0_receipt_sha256": r.B0ReactivationReceiptSHA256, "reversal_sha256": r.ReversalSHA256, "fresh_ordinary_plan_sha256": "", "compatible_intent_sha256": ""}
	receiptBody, _ := json.Marshal(receipt)
	n := &ColdAdminIdentity{IntentSHA256: r.IntentSHA256, RoutingEpoch: r.RoutingEpoch, PlanSHA256: r.PlanSHA256, ReversalSHA256: r.ReversalSHA256, RetirementEpoch: r.RetirementEpoch, B0TargetSHA256: r.TargetSHA256, OrdinaryRestorationPlanSHA256: r.OrdinaryRestorationPlanSHA256, B0ReactivationPlanSHA256: r.B0ReactivationPlanSHA256, B0ReactivationReceiptSHA256: r.B0ReactivationReceiptSHA256, OrdinaryRestorationMode: "legacy", OrdinaryFinalizationPlanSHA256: r.FinalizationPlanSHA256, OrdinaryFinalizationPlan: plan, OrdinaryFinalizationPhase: "complete", OrdinaryPublicationReceiptSHA256: publication, OrdinaryFinalizationReceipt: receiptBody, OrdinaryFinalizationReceiptSHA256: hostDigest(receiptBody)}
	prior := *n
	prior.OrdinaryFinalizationPhase = "published"
	prior.OrdinaryFinalizationReceipt, prior.OrdinaryFinalizationReceiptSHA256 = nil, ""
	parent := &HostColdPhaseResult{Outcome: "completed", Native: &prior}
	if validateHostColdCompletionResult(r, n, inputs, parent) != nil {
		t.Fatal("exact joined completion refused")
	}
	for _, key := range []string{"version", "plan_sha256", "source_revision", "retirement_epoch", "publication_receipt_sha256", "b0_receipt_sha256", "reversal_sha256", "fresh_ordinary_plan_sha256", "compatible_intent_sha256"} {
		altered := map[string]any{}
		for k, v := range receipt {
			altered[k] = v
		}
		altered[key] = strings.Repeat("c", 64)
		if key == "source_revision" {
			altered[key] = strings.Repeat("c", 40)
		}
		if key == "retirement_epoch" {
			altered[key] = r.RetirementEpoch + 1
		}
		bad := *n
		bad.OrdinaryFinalizationReceipt, _ = json.Marshal(altered)
		bad.OrdinaryFinalizationReceiptSHA256 = hostDigest(bad.OrdinaryFinalizationReceipt)
		if validateHostColdCompletionResult(r, &bad, inputs, parent) == nil {
			t.Fatal("rehashed foreign completion receipt admitted", key)
		}
	}
	bad := *n
	bad.RestoredOrdinaryPlanSHA256 = strings.Repeat("c", 64)
	if validateHostColdCompletionResult(r, &bad, inputs, parent) == nil {
		t.Fatal("legacy completion granted native ordinary authority")
	}
	r = hostCompletionTestRequest("cold-b0-reactivation-plan")
	historical := []byte("exact prior receipt\n")
	r.PriorB0ReceiptSHA256 = hostDigest(historical)
	inputs = map[string][]byte{"cold-input-" + r.PriorB0ReceiptSHA256: historical}
	reactivation := map[string]any{"version": "jobseek.crawler.cold-b0-reactivation/v1", "request": queue.ColdB0ReactivationRequest{OrdinaryRestorationPlanSHA256: r.OrdinaryRestorationPlanSHA256, SourceRevision: r.Binding.SourceRevision}, "retirement_epoch": r.RetirementEpoch, "rollback_receipt": string(historical), "rollback_receipt_sha256": r.PriorB0ReceiptSHA256, "forward": map[string]string{"target_sha256": r.TargetSHA256}}
	plan, _ = json.Marshal(reactivation)
	n = &ColdAdminIdentity{IntentSHA256: r.IntentSHA256, RoutingEpoch: r.RoutingEpoch, PlanSHA256: r.PlanSHA256, ReversalSHA256: r.ReversalSHA256, RetirementEpoch: r.RetirementEpoch, B0TargetSHA256: r.TargetSHA256, OrdinaryRestorationPlanSHA256: r.OrdinaryRestorationPlanSHA256, B0ReactivationPlan: plan, B0ReactivationPlanSHA256: hostDigest(plan)}
	if validateHostColdCompletionResult(r, n, inputs, nil) != nil {
		t.Fatal("exact reactivation preview refused")
	}
	reactivation["rollback_receipt"] = "foreign receipt\n"
	reactivation["rollback_receipt_sha256"] = hostDigest([]byte("foreign receipt\n"))
	bad = *n
	bad.B0ReactivationPlan, _ = json.Marshal(reactivation)
	bad.B0ReactivationPlanSHA256 = hostDigest(bad.B0ReactivationPlan)
	if validateHostColdCompletionResult(r, &bad, inputs, nil) == nil {
		t.Fatal("rehashed foreign historical receipt admitted")
	}
}

func TestHostJournalLongHistoryRecoversPendingPublication(t *testing.T) {
	s, err := openHostStore(hostPrivateDirectory(t))
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	body := []byte(`{"retained":"fixture"}`)
	for n := 0; n < 320; n++ {
		if err := s.retain(fmt.Sprintf("cold-completed-%064x.json", n), body, nil); err != nil {
			t.Fatal("owned bounded history retention", err)
		}
	}
	temporary := ".host-" + strings.Repeat("a", 32)
	f, err := s.root.OpenFile(temporary, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.Write(body); err != nil {
		t.Fatal(err)
	}
	if f.Sync() != nil || f.Close() != nil || s.root.Link(temporary, "recovered-result") != nil {
		t.Fatal("owned pending publication")
	}
	if s.retain("recovered-result", body, nil) != nil {
		t.Fatal("supported long history refused exact pending-link recovery")
	}
	if _, err := s.root.Lstat(temporary); !os.IsNotExist(err) {
		t.Fatal("pending link survived completion")
	}
	got, err := s.read("recovered-result", false)
	if err != nil || !bytes.Equal(got, body) {
		t.Fatal("long-history recovery changed result")
	}
}
