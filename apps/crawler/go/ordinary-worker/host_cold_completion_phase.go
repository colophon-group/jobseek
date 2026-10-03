package worker

import (
	"bytes"
	"encoding/json"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func hostColdCompletionOperation(operation string) bool {
	return coldB0ReactivationOperation(operation) || coldOrdinaryFinalizationOperation(operation)
}

func hostColdCompletionInputsEmpty(r HostColdPhaseRequest) bool {
	return r.PriorB0ReceiptSHA256 == "" && r.B0ReactivationPlanSHA256 == "" && r.B0ReactivationReceiptSHA256 == "" && r.FinalizationRequestSHA256 == "" && r.FinalizationPlanSHA256 == ""
}

func hostColdResultLimit(operation string) int64 {
	if coldB0ReactivationOperation(operation) {
		return 96 << 20
	}
	return 48 << 20
}

func validateHostColdCompletionRequest(r HostColdPhaseRequest) error {
	if !hostColdCompletionOperation(r.Operation) || !planPattern.MatchString(r.PredecessorSHA256) || r.RoutingEpoch <= r.PreviousEpoch || r.RetirementEpoch <= r.RoutingEpoch || r.RetirementEpoch > 9999999999999 || !planPattern.MatchString(r.IntentSHA256) || !planPattern.MatchString(r.PlanSHA256) || !planPattern.MatchString(r.ReversalSHA256) || !planPattern.MatchString(r.OrdinaryRestorationPlanSHA256) || r.Namespace != "" || r.Shard != "" || r.Cohort != "" || r.ForwardRequestSHA256 != "" || r.ForwardPlanSHA256 != "" || r.ForwardReceiptSHA256 != "" || r.RestoreRequestSHA256 != "" || r.B0RollbackPlanSHA256 != "" || r.OrdinaryRequestSHA256 != "" {
		return errHostPreflight
	}
	inspect := r.Operation == "cold-b0-reactivation-inspect" || r.Operation == "cold-ordinary-finalization-inspect"
	if inspect {
		if r.TargetSHA256 != "" || r.LuaSHA256 != "" {
			return errHostPreflight
		}
	} else if !planPattern.MatchString(r.TargetSHA256) || !planPattern.MatchString(r.LuaSHA256) {
		return errHostPreflight
	}
	if coldB0ReactivationOperation(r.Operation) {
		if r.FinalizationRequestSHA256 != "" || r.FinalizationPlanSHA256 != "" || r.B0ReactivationReceiptSHA256 != "" {
			return errHostPreflight
		}
		if inspect {
			if r.PriorB0ReceiptSHA256 != "" {
				return errHostPreflight
			}
		} else if !planPattern.MatchString(r.PriorB0ReceiptSHA256) {
			return errHostPreflight
		}
		if r.Operation == "cold-b0-reactivation-plan" {
			if r.B0ReactivationPlanSHA256 != "" {
				return errHostPreflight
			}
		} else if !planPattern.MatchString(r.B0ReactivationPlanSHA256) {
			return errHostPreflight
		}
	} else {
		if r.PriorB0ReceiptSHA256 != "" || !planPattern.MatchString(r.B0ReactivationPlanSHA256) || !planPattern.MatchString(r.B0ReactivationReceiptSHA256) || !planPattern.MatchString(r.FinalizationRequestSHA256) {
			return errHostPreflight
		}
		if r.Operation == "cold-ordinary-finalization-plan" {
			if r.FinalizationPlanSHA256 != "" {
				return errHostPreflight
			}
		} else if !planPattern.MatchString(r.FinalizationPlanSHA256) {
			return errHostPreflight
		}
	}
	return nil
}

func hostColdCompletionPrior(operation string) string {
	switch operation {
	case "cold-b0-reactivation-plan":
		return "cold-ordinary-rollback-inspect"
	case "cold-b0-reactivation-retain":
		return "cold-b0-reactivation-plan"
	case "cold-b0-reactivation-apply":
		return "cold-b0-reactivation-retain"
	case "cold-b0-reactivation-inspect":
		return "cold-b0-reactivation-apply"
	case "cold-ordinary-finalization-plan":
		return "cold-b0-reactivation-inspect"
	case "cold-ordinary-finalization-retain":
		return "cold-ordinary-finalization-plan"
	case "cold-ordinary-finalization-prepare":
		return "cold-ordinary-finalization-retain"
	case "cold-ordinary-finalization-publish":
		return "cold-ordinary-finalization-prepare"
	case "cold-ordinary-finalization-complete":
		return "cold-ordinary-finalization-publish"
	case "cold-ordinary-finalization-inspect":
		return "cold-ordinary-finalization-complete"
	}
	return ""
}

func checkHostColdCompletionLink(r, p HostColdPhaseRequest, parent *HostColdPhaseResult) error {
	if validateHostColdCompletionRequest(r) != nil || parent == nil || parent.Outcome != "completed" || parent.Native == nil || p.Operation != hostColdCompletionPrior(r.Operation) || p.IntentSHA256 != r.IntentSHA256 || p.ReversalSHA256 != r.ReversalSHA256 || p.RoutingEpoch != r.RoutingEpoch || p.PlanSHA256 != r.PlanSHA256 || p.RetirementEpoch != r.RetirementEpoch {
		return errHostPreflight
	}
	n := parent.Native
	if n.Version != "jobseek.ordinary.cold-identity/v1" || n.Operation != p.Operation || n.SourceRevision != r.Binding.SourceRevision || n.IntentSHA256 != r.IntentSHA256 || n.ReversalSHA256 != r.ReversalSHA256 || n.RoutingEpoch != r.RoutingEpoch || n.PlanSHA256 != r.PlanSHA256 || n.RetirementEpoch != r.RetirementEpoch || n.OrdinaryRestorationPlanSHA256 != r.OrdinaryRestorationPlanSHA256 {
		return errHostPreflight
	}
	if r.Operation == "cold-b0-reactivation-plan" {
		if hostDigest(n.OrdinaryRestorationPlan) != r.OrdinaryRestorationPlanSHA256 {
			return errHostPreflight
		}
	} else if n.B0ReactivationPlanSHA256 != r.B0ReactivationPlanSHA256 {
		return errHostPreflight
	}
	if coldB0ReactivationOperation(r.Operation) && r.Operation != "cold-b0-reactivation-plan" {
		if len(n.B0ReactivationPlan) == 0 || hostDigest(n.B0ReactivationPlan) != r.B0ReactivationPlanSHA256 || r.Operation != "cold-b0-reactivation-inspect" && p.PriorB0ReceiptSHA256 != r.PriorB0ReceiptSHA256 {
			return errHostPreflight
		}
		if r.Operation == "cold-b0-reactivation-inspect" && (n.B0ReactivationPhase != "redis-reactivated" || !planPattern.MatchString(n.B0ReactivationReceiptSHA256)) {
			return errHostPreflight
		}
		if r.Operation != "cold-b0-reactivation-inspect" && (n.B0ReactivationPhase != "" || n.B0ReactivationReceiptSHA256 != "") {
			return errHostPreflight
		}
	}
	if coldOrdinaryFinalizationOperation(r.Operation) {
		if n.B0ReactivationReceiptSHA256 != r.B0ReactivationReceiptSHA256 {
			return errHostPreflight
		}
		if r.Operation == "cold-ordinary-finalization-plan" {
			if n.B0ReactivationPhase != "redis-reactivated" {
				return errHostPreflight
			}
		} else if p.FinalizationRequestSHA256 != r.FinalizationRequestSHA256 || n.OrdinaryFinalizationPlanSHA256 != r.FinalizationPlanSHA256 || hostDigest(n.OrdinaryFinalizationPlan) != r.FinalizationPlanSHA256 || n.OrdinaryFinalizationPhase != hostColdFinalizationPriorPhase(r.Operation) {
			return errHostPreflight
		}
	}
	return nil
}

func hostColdFinalizationPriorPhase(operation string) string {
	switch operation {
	case "cold-ordinary-finalization-retain":
		return ""
	case "cold-ordinary-finalization-prepare":
		return ""
	case "cold-ordinary-finalization-publish":
		return "publishing"
	case "cold-ordinary-finalization-complete":
		return "published"
	case "cold-ordinary-finalization-inspect":
		return "complete"
	}
	return ""
}

func (s *hostColdPhaseScope) checkCompletionInputs(r HostColdPhaseRequest, intent queue.ColdTransitionSpec, inputs map[string][]byte) error {
	if coldB0ReactivationOperation(r.Operation) {
		if r.Operation != "cold-b0-reactivation-inspect" && (r.PriorB0ReceiptSHA256 != intent.PreviousB0ReceiptSHA256 || hostDigest(inputs["cold-input-"+r.PriorB0ReceiptSHA256]) != r.PriorB0ReceiptSHA256) {
			return errHostPreflight
		}
		return nil
	}
	request, err := queue.DecodeColdOrdinaryFinalizationRequest(string(inputs["cold-input-"+r.FinalizationRequestSHA256]), r.FinalizationRequestSHA256)
	if err != nil || request.SourceRevision != r.Binding.SourceRevision || request.OrdinaryRestorationPlanSHA256 != r.OrdinaryRestorationPlanSHA256 || request.B0ReactivationPlanSHA256 != r.B0ReactivationPlanSHA256 || request.ActiveReleaseSHA256 != s.info.ActiveReleaseSHA256 || request.ColdAttestationSHA256 != s.info.ColdAttestationSHA256 || request.TransitionID == intent.TransitionID {
		return errHostPreflight
	}
	return nil
}

func validateHostColdCompletionResult(r HostColdPhaseRequest, n *ColdAdminIdentity, inputs map[string][]byte, parent *HostColdPhaseResult) error {
	if validateHostColdCompletionRequest(r) != nil || n.IntentSHA256 != r.IntentSHA256 || n.RoutingEpoch != r.RoutingEpoch || n.PlanSHA256 != r.PlanSHA256 || n.ReversalSHA256 != r.ReversalSHA256 || n.RetirementEpoch != r.RetirementEpoch || n.B0TargetSHA256 != r.TargetSHA256 || n.OrdinaryRestorationPlanSHA256 != r.OrdinaryRestorationPlanSHA256 {
		return errHostPreflight
	}
	if coldB0ReactivationOperation(r.Operation) {
		var plan struct {
			Version    string                          `json:"version"`
			Request    queue.ColdB0ReactivationRequest `json:"request"`
			Epoch      int64                           `json:"retirement_epoch"`
			ReceiptSHA string                          `json:"rollback_receipt_sha256"`
			Receipt    string                          `json:"rollback_receipt"`
			Forward    struct {
				Target string `json:"target_sha256"`
			} `json:"forward"`
		}
		if len(n.B0ReactivationPlan) == 0 || len(n.B0ReactivationPlan) > 64<<20 || hostDigest(n.B0ReactivationPlan) != n.B0ReactivationPlanSHA256 || json.Unmarshal(n.B0ReactivationPlan, &plan) != nil || plan.Request != (queue.ColdB0ReactivationRequest{OrdinaryRestorationPlanSHA256: r.OrdinaryRestorationPlanSHA256, SourceRevision: r.Binding.SourceRevision}) || plan.Epoch != r.RetirementEpoch || hostDigest([]byte(plan.Receipt)) != plan.ReceiptSHA {
			return errHostPreflight
		}
		if r.Operation != "cold-b0-reactivation-inspect" && (plan.ReceiptSHA != r.PriorB0ReceiptSHA256 || !bytes.Equal([]byte(plan.Receipt), inputs["cold-input-"+r.PriorB0ReceiptSHA256])) {
			return errHostPreflight
		}
		if plan.Version != "jobseek.crawler.cold-b0-reactivation/v1" || r.Operation != "cold-b0-reactivation-inspect" && plan.Forward.Target != r.TargetSHA256 {
			return errHostPreflight
		}
		if r.Operation != "cold-b0-reactivation-plan" && (n.B0ReactivationPlanSHA256 != r.B0ReactivationPlanSHA256 || parent == nil || parent.Native == nil || !bytes.Equal(n.B0ReactivationPlan, parent.Native.B0ReactivationPlan)) {
			return errHostPreflight
		}
		switch r.Operation {
		case "cold-b0-reactivation-plan", "cold-b0-reactivation-retain":
			if n.B0ReactivationPhase != "" || n.B0ReactivationReceiptSHA256 != "" {
				return errHostPreflight
			}
		default:
			if n.B0ReactivationPhase != "redis-reactivated" || !planPattern.MatchString(n.B0ReactivationReceiptSHA256) || r.Operation == "cold-b0-reactivation-inspect" && n.B0ReactivationReceiptSHA256 != parent.Native.B0ReactivationReceiptSHA256 {
				return errHostPreflight
			}
		}
		return nil
	}
	request, err := queue.DecodeColdOrdinaryFinalizationRequest(string(inputs["cold-input-"+r.FinalizationRequestSHA256]), r.FinalizationRequestSHA256)
	var plan struct {
		Version          string                                `json:"version"`
		Request          queue.ColdOrdinaryFinalizationRequest `json:"request"`
		Reversal         string                                `json:"reversal_sha256"`
		Epoch            int64                                 `json:"retirement_epoch"`
		Mode             string                                `json:"mode"`
		B0Receipt        string                                `json:"b0_receipt_sha256"`
		Target           string                                `json:"target_sha256"`
		FreshOrdinary    string                                `json:"fresh_ordinary_plan_sha256"`
		CompatibleIntent string                                `json:"compatible_intent_sha256"`
	}
	if err != nil || len(n.OrdinaryFinalizationPlan) == 0 || len(n.OrdinaryFinalizationPlan) > 32<<20 || hostDigest(n.OrdinaryFinalizationPlan) != n.OrdinaryFinalizationPlanSHA256 || json.Unmarshal(n.OrdinaryFinalizationPlan, &plan) != nil || plan.Request != request || plan.Reversal != r.ReversalSHA256 || plan.Epoch != r.RetirementEpoch || plan.Mode != n.OrdinaryRestorationMode || plan.Mode != "legacy" && plan.Mode != "native" || plan.B0Receipt != r.B0ReactivationReceiptSHA256 || n.B0ReactivationPlanSHA256 != r.B0ReactivationPlanSHA256 || n.B0ReactivationReceiptSHA256 != r.B0ReactivationReceiptSHA256 {
		return errHostPreflight
	}
	if r.Operation != "cold-ordinary-finalization-inspect" && plan.Target != r.TargetSHA256 {
		return errHostPreflight
	}
	if plan.Version != "jobseek.crawler.cold-ordinary-finalization/v1" {
		return errHostPreflight
	}
	if plan.Mode == "legacy" {
		if plan.FreshOrdinary != "" || plan.CompatibleIntent != "" || n.RestoredOrdinaryPlanSHA256 != "" || n.RestoredOrdinaryProjectionSHA1 != "" || n.RestoredOrdinarySourceRevision != "" {
			return errHostPreflight
		}
	} else if plan.FreshOrdinary != n.RestoredOrdinaryPlanSHA256 || !planPattern.MatchString(plan.FreshOrdinary) || !planPattern.MatchString(plan.CompatibleIntent) || !sourcePattern.MatchString(n.RestoredOrdinarySourceRevision) || !sourcePattern.MatchString(n.RestoredOrdinaryProjectionSHA1) {
		return errHostPreflight
	}
	if r.Operation != "cold-ordinary-finalization-plan" && (n.OrdinaryFinalizationPlanSHA256 != r.FinalizationPlanSHA256 || parent == nil || parent.Native == nil || !bytes.Equal(n.OrdinaryFinalizationPlan, parent.Native.OrdinaryFinalizationPlan)) {
		return errHostPreflight
	}
	if r.Operation != "cold-ordinary-finalization-plan" && (n.OrdinaryRestorationMode != parent.Native.OrdinaryRestorationMode || n.RestoredOrdinaryPlanSHA256 != parent.Native.RestoredOrdinaryPlanSHA256 || n.RestoredOrdinaryProjectionSHA1 != parent.Native.RestoredOrdinaryProjectionSHA1 || n.RestoredOrdinarySourceRevision != parent.Native.RestoredOrdinarySourceRevision) {
		return errHostPreflight
	}
	phase := ""
	switch r.Operation {
	case "cold-ordinary-finalization-prepare":
		phase = "publishing"
	case "cold-ordinary-finalization-publish":
		phase = "published"
	case "cold-ordinary-finalization-complete", "cold-ordinary-finalization-inspect":
		phase = "complete"
	}
	if n.OrdinaryFinalizationPhase != phase {
		return errHostPreflight
	}
	if phase == "published" || phase == "complete" {
		if !planPattern.MatchString(n.OrdinaryPublicationReceiptSHA256) {
			return errHostPreflight
		}
	} else if n.OrdinaryPublicationReceiptSHA256 != "" {
		return errHostPreflight
	}
	if phase == "complete" {
		if parent == nil || parent.Native == nil || n.OrdinaryPublicationReceiptSHA256 != parent.Native.OrdinaryPublicationReceiptSHA256 {
			return errHostPreflight
		}
		if !planPattern.MatchString(n.OrdinaryFinalizationReceiptSHA256) || hostDigest(n.OrdinaryFinalizationReceipt) != n.OrdinaryFinalizationReceiptSHA256 {
			return errHostPreflight
		}
		var receipt struct {
			Version          string `json:"version"`
			Plan             string `json:"plan_sha256"`
			Source           string `json:"source_revision"`
			Epoch            int64  `json:"retirement_epoch"`
			Publication      string `json:"publication_receipt_sha256"`
			B0               string `json:"b0_receipt_sha256"`
			Reversal         string `json:"reversal_sha256"`
			FreshOrdinary    string `json:"fresh_ordinary_plan_sha256"`
			CompatibleIntent string `json:"compatible_intent_sha256"`
		}
		if json.Unmarshal(n.OrdinaryFinalizationReceipt, &receipt) != nil || receipt.Plan != n.OrdinaryFinalizationPlanSHA256 || receipt.Source != r.Binding.SourceRevision || receipt.Epoch != r.RetirementEpoch || receipt.Publication != n.OrdinaryPublicationReceiptSHA256 || receipt.B0 != r.B0ReactivationReceiptSHA256 || receipt.Reversal != r.ReversalSHA256 {
			return errHostPreflight
		}
		if receipt.Version != "jobseek.crawler.cold-ordinary-completion/v1" || receipt.FreshOrdinary != plan.FreshOrdinary || receipt.CompatibleIntent != plan.CompatibleIntent {
			return errHostPreflight
		}
		if r.Operation == "cold-ordinary-finalization-inspect" && (n.OrdinaryFinalizationReceiptSHA256 != parent.Native.OrdinaryFinalizationReceiptSHA256 || !bytes.Equal(n.OrdinaryFinalizationReceipt, parent.Native.OrdinaryFinalizationReceipt)) {
			return errHostPreflight
		}
	} else if n.OrdinaryFinalizationReceiptSHA256 != "" || len(n.OrdinaryFinalizationReceipt) != 0 {
		return errHostPreflight
	}
	return nil
}
