package worker

import (
	"bytes"
	"encoding/json"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func hostColdRestorationOperation(operation string) bool {
	return coldB0RestorationOperation(operation) || coldOrdinaryRestorationOperation(operation)
}

func hostColdRetirementOperation(operation string) bool {
	return hostColdReversalOperation(operation) || hostColdRestorationOperation(operation) || hostColdCompletionOperation(operation)
}

func hostColdRestorationInputsEmpty(r HostColdPhaseRequest) bool {
	return r.RestoreRequestSHA256 == "" && r.B0RollbackPlanSHA256 == "" && r.OrdinaryRequestSHA256 == "" && r.OrdinaryRestorationPlanSHA256 == ""
}

func validateHostColdRestorationRequest(r HostColdPhaseRequest) error {
	if !hostColdRestorationOperation(r.Operation) || !planPattern.MatchString(r.PredecessorSHA256) || r.RoutingEpoch <= r.PreviousEpoch || r.RetirementEpoch <= r.RoutingEpoch || r.RetirementEpoch > 9999999999999 || !planPattern.MatchString(r.IntentSHA256) || !planPattern.MatchString(r.PlanSHA256) || !planPattern.MatchString(r.ReversalSHA256) || r.Namespace != "" || r.Shard != "" || r.Cohort != "" || r.ForwardRequestSHA256 != "" || r.ForwardPlanSHA256 != "" || r.ForwardReceiptSHA256 != "" {
		return errHostPreflight
	}
	if r.Operation == "cold-b0-rollback-inspect" || r.Operation == "cold-ordinary-rollback-inspect" {
		if r.TargetSHA256 != "" || r.LuaSHA256 != "" {
			return errHostPreflight
		}
	} else if !planPattern.MatchString(r.TargetSHA256) || !planPattern.MatchString(r.LuaSHA256) {
		return errHostPreflight
	}
	if coldB0RestorationOperation(r.Operation) {
		if !planPattern.MatchString(r.RestoreRequestSHA256) || r.OrdinaryRequestSHA256 != "" || r.OrdinaryRestorationPlanSHA256 != "" {
			return errHostPreflight
		}
		if r.Operation == "cold-b0-rollback-plan" {
			if r.B0RollbackPlanSHA256 != "" {
				return errHostPreflight
			}
		} else if !planPattern.MatchString(r.B0RollbackPlanSHA256) {
			return errHostPreflight
		}
	} else {
		if r.RestoreRequestSHA256 != "" || !planPattern.MatchString(r.OrdinaryRequestSHA256) || !planPattern.MatchString(r.B0RollbackPlanSHA256) {
			return errHostPreflight
		}
		if r.Operation == "cold-ordinary-rollback-plan" {
			if r.OrdinaryRestorationPlanSHA256 != "" {
				return errHostPreflight
			}
		} else if !planPattern.MatchString(r.OrdinaryRestorationPlanSHA256) {
			return errHostPreflight
		}
	}
	return nil
}

func hostColdRestorationPrior(operation string) string {
	switch operation {
	case "cold-b0-rollback-plan":
		return "cold-reversal-inspect"
	case "cold-b0-rollback-retain":
		return "cold-b0-rollback-plan"
	case "cold-b0-rollback-restore":
		return "cold-b0-rollback-retain"
	case "cold-b0-rollback-inspect":
		return "cold-b0-rollback-restore"
	case "cold-ordinary-rollback-plan":
		return "cold-b0-rollback-inspect"
	case "cold-ordinary-rollback-retain":
		return "cold-ordinary-rollback-plan"
	case "cold-ordinary-rollback-inspect":
		return "cold-ordinary-rollback-retain"
	}
	return ""
}

func checkHostColdRestorationLink(r, p HostColdPhaseRequest, parent *HostColdPhaseResult) error {
	if validateHostColdRestorationRequest(r) != nil || parent == nil || parent.Outcome != "completed" || parent.Native == nil || p.Operation != hostColdRestorationPrior(r.Operation) || p.IntentSHA256 != r.IntentSHA256 || p.ReversalSHA256 != r.ReversalSHA256 || p.RoutingEpoch != r.RoutingEpoch || p.PlanSHA256 != r.PlanSHA256 || p.RetirementEpoch != r.RetirementEpoch {
		return errHostPreflight
	}
	n := parent.Native
	if n.Version != "jobseek.ordinary.cold-identity/v1" || n.Operation != p.Operation || n.SourceRevision != r.Binding.SourceRevision || n.IntentSHA256 != r.IntentSHA256 || n.ReversalSHA256 != r.ReversalSHA256 || n.RoutingEpoch != r.RoutingEpoch || n.PlanSHA256 != r.PlanSHA256 || n.RetirementEpoch != r.RetirementEpoch {
		return errHostPreflight
	}
	if r.Operation == "cold-b0-rollback-plan" {
		if n.ReversalPhase != "reserved" {
			return errHostPreflight
		}
	} else {
		if n.B0RollbackPlanSHA256 != r.B0RollbackPlanSHA256 {
			return errHostPreflight
		}
		if coldB0RestorationOperation(r.Operation) && p.RestoreRequestSHA256 != r.RestoreRequestSHA256 {
			return errHostPreflight
		}
	}
	switch r.Operation {
	case "cold-b0-rollback-restore":
		if n.B0RestorationPhase != "prepared" {
			return errHostPreflight
		}
	case "cold-b0-rollback-inspect", "cold-ordinary-rollback-plan":
		if n.B0RestorationPhase != "fences-cleared" {
			return errHostPreflight
		}
	case "cold-ordinary-rollback-retain", "cold-ordinary-rollback-inspect":
		if p.OrdinaryRequestSHA256 != r.OrdinaryRequestSHA256 || n.OrdinaryRestorationPlanSHA256 != r.OrdinaryRestorationPlanSHA256 || hostDigest(n.OrdinaryRestorationPlan) != r.OrdinaryRestorationPlanSHA256 {
			return errHostPreflight
		}
	}
	return nil
}

func (s *hostColdPhaseScope) restorationPredecessor(r, p HostColdPhaseRequest, parent *HostColdPhaseResult) error {
	if checkHostColdRestorationLink(r, p, parent) != nil {
		return errHostPreflight
	}
	return s.retirementAncestry(r)
}

func validateHostColdRestorationInputs(r HostColdPhaseRequest, intent queue.ColdTransitionSpec, inputs map[string][]byte) error {
	if coldB0RestorationOperation(r.Operation) {
		request, err := queue.DecodeColdB0RollbackRequest(string(inputs["cold-input-"+r.RestoreRequestSHA256]), r.RestoreRequestSHA256)
		if err != nil || request.SourceRevision != r.Binding.SourceRevision || request.ReversalSHA256 != r.ReversalSHA256 || request.RetirementEpoch != r.RetirementEpoch {
			return errHostPreflight
		}
		if request.B0SourceEpoch != r.RoutingEpoch {
			reversal, err := queue.DecodeColdReversalSpec(string(inputs["cold-input-"+r.ReversalSHA256]), r.ReversalSHA256)
			if err != nil || request.B0SourceEpoch != r.PreviousEpoch || reversal.SourcePhase != "reserved" && reversal.SourcePhase != "publishing" {
				return errHostPreflight
			}
		}
	} else {
		request, err := queue.DecodeColdOrdinaryRestorationRequest(string(inputs["cold-input-"+r.OrdinaryRequestSHA256]), r.OrdinaryRequestSHA256)
		if err != nil || request.SourceRevision != r.Binding.SourceRevision || request.ReversalSHA256 != r.ReversalSHA256 || request.RetirementEpoch != r.RetirementEpoch || request.B0RestorationPlanSHA256 != r.B0RollbackPlanSHA256 || (request.RollbackSourceRevision == "") != (intent.PreviousOrdinaryPlanSHA256 == "") {
			return errHostPreflight
		}
	}
	return nil
}

func (s *hostColdPhaseScope) checkRestorationSourceReceipt(r HostColdPhaseRequest, intent queue.ColdTransitionSpec, inputs map[string][]byte) error {
	request, err := queue.DecodeColdB0RollbackRequest(string(inputs["cold-input-"+r.RestoreRequestSHA256]), r.RestoreRequestSHA256)
	if err != nil {
		return errHostPreflight
	}
	if request.B0SourceEpoch == r.PreviousEpoch {
		if request.SourceReceiptSHA256 != intent.PreviousB0ReceiptSHA256 {
			return errHostPreflight
		}
		return nil
	}
	// Candidate N must name the transfer receipt retained by this exact forward
	// ancestry. An arbitrary syntactically valid receipt hash cannot stand in.
	for depth := 0; depth < 64; depth++ {
		prior, parent, err := s.loadPredecessor(r)
		if err != nil || prior == nil || parent == nil {
			return errHostPreflight
		}
		if parent.Outcome == "completed" && coldB0ForwardOperation(prior.Operation) && parent.Native.B0ForwardPhase == "redis-transferred" {
			n := parent.Native
			if n.IntentSHA256 != r.IntentSHA256 || n.RoutingEpoch != request.B0SourceEpoch || n.B0ForwardReceiptSHA256 != request.SourceReceiptSHA256 {
				return errHostPreflight
			}
			return nil
		}
		r = *prior
	}
	return errHostPreflight
}

func validateHostColdRestorationResult(r HostColdPhaseRequest, n *ColdAdminIdentity, inputs map[string][]byte, parent *HostColdPhaseResult) error {
	if validateHostColdRestorationRequest(r) != nil || n.IntentSHA256 != r.IntentSHA256 || n.RoutingEpoch != r.RoutingEpoch || n.PlanSHA256 != r.PlanSHA256 || n.ReversalSHA256 != r.ReversalSHA256 || n.RetirementEpoch != r.RetirementEpoch || n.B0TargetSHA256 != r.TargetSHA256 {
		return errHostPreflight
	}
	if coldB0RestorationOperation(r.Operation) {
		if !planPattern.MatchString(n.B0RollbackPlanSHA256) {
			return errHostPreflight
		}
		if r.Operation == "cold-b0-rollback-plan" {
			var plan struct {
				Request queue.ColdB0RollbackRequest `json:"request"`
				Target  string                      `json:"target_sha256"`
			}
			request, err := queue.DecodeColdB0RollbackRequest(string(inputs["cold-input-"+r.RestoreRequestSHA256]), r.RestoreRequestSHA256)
			if err != nil || len(n.B0RollbackPlan) == 0 || len(n.B0RollbackPlan) > 32<<20 || hostDigest(n.B0RollbackPlan) != n.B0RollbackPlanSHA256 || json.Unmarshal(n.B0RollbackPlan, &plan) != nil || plan.Request != request || plan.Target != r.TargetSHA256 || n.B0RestorationPhase != "" {
				return errHostPreflight
			}
		} else {
			phase := "fences-cleared"
			if r.Operation == "cold-b0-rollback-retain" {
				phase = "prepared"
			}
			if n.B0RollbackPlanSHA256 != r.B0RollbackPlanSHA256 || n.B0RestorationPhase != phase || len(n.B0RollbackPlan) != 0 {
				return errHostPreflight
			}
		}
	} else {
		request, err := queue.DecodeColdOrdinaryRestorationRequest(string(inputs["cold-input-"+r.OrdinaryRequestSHA256]), r.OrdinaryRequestSHA256)
		var plan struct {
			Request queue.ColdOrdinaryRestorationRequest `json:"request"`
			Mode    string                               `json:"mode"`
		}
		if err != nil || n.B0RollbackPlanSHA256 != r.B0RollbackPlanSHA256 || !planPattern.MatchString(n.OrdinaryRestorationPlanSHA256) || len(n.OrdinaryRestorationPlan) == 0 || len(n.OrdinaryRestorationPlan) > 32<<20 || hostDigest(n.OrdinaryRestorationPlan) != n.OrdinaryRestorationPlanSHA256 || json.Unmarshal(n.OrdinaryRestorationPlan, &plan) != nil || plan.Request != request || plan.Mode != n.OrdinaryRestorationMode || plan.Mode != "legacy" && plan.Mode != "native" {
			return errHostPreflight
		}
		if r.Operation != "cold-ordinary-rollback-plan" && (n.OrdinaryRestorationPlanSHA256 != r.OrdinaryRestorationPlanSHA256 || parent == nil || parent.Native == nil || !bytes.Equal(n.OrdinaryRestorationPlan, parent.Native.OrdinaryRestorationPlan)) {
			return errHostPreflight
		}
	}
	return nil
}
