package worker

import "bytes"

// The host journal deliberately exposes only the B0-aware publication path.
// Ordinary-only cold-prepare/publish/activate cannot bypass producer transfer.
func validateHostColdForwardRequest(r HostColdPhaseRequest) error {
	if !coldB0ForwardOperation(r.Operation) || r.PredecessorSHA256 == "" || r.RoutingEpoch <= r.PreviousEpoch || r.RoutingEpoch > 9999999999999 || !planPattern.MatchString(r.IntentSHA256) || !planPattern.MatchString(r.PlanSHA256) || !planPattern.MatchString(r.ForwardRequestSHA256) || r.Namespace != "" || r.Shard != "" || r.Cohort != "" {
		return errHostPreflight
	}
	if r.Operation == "cold-b0-forward-inspect" {
		if r.TargetSHA256 != "" || r.LuaSHA256 != "" {
			return errHostPreflight
		}
	} else if !planPattern.MatchString(r.TargetSHA256) || !planPattern.MatchString(r.LuaSHA256) {
		return errHostPreflight
	}
	if r.Operation == "cold-b0-forward-plan" {
		if r.ForwardPlanSHA256 != "" {
			return errHostPreflight
		}
	} else if !planPattern.MatchString(r.ForwardPlanSHA256) {
		return errHostPreflight
	}
	if coldForwardPublicationOperation(r.Operation) {
		if !planPattern.MatchString(r.ForwardReceiptSHA256) {
			return errHostPreflight
		}
	} else if r.ForwardReceiptSHA256 != "" {
		return errHostPreflight
	}
	return nil
}

func hostColdForwardPrior(operation string) string {
	switch operation {
	case "cold-b0-forward-plan":
		return "cold-inspect"
	case "cold-b0-forward-retain":
		return "cold-b0-forward-plan"
	case "cold-b0-forward-apply":
		return "cold-b0-forward-retain"
	case "cold-b0-forward-inspect":
		return "cold-b0-forward-apply"
	case "cold-forward-prepare":
		return "cold-b0-forward-inspect"
	case "cold-forward-publish":
		return "cold-forward-prepare"
	case "cold-forward-activate":
		return "cold-forward-publish"
	}
	return ""
}

func checkHostColdForwardLink(r, prior HostColdPhaseRequest, parent *HostColdPhaseResult) error {
	if validateHostColdForwardRequest(r) != nil || parent == nil || parent.Outcome != "completed" || parent.Native == nil || prior.Operation != hostColdForwardPrior(r.Operation) {
		return errHostPreflight
	}
	n := parent.Native
	if n.Version != "jobseek.ordinary.cold-identity/v1" || n.Operation != prior.Operation || n.SourceRevision != r.Binding.SourceRevision || n.IntentSHA256 != r.IntentSHA256 || prior.IntentSHA256 != r.IntentSHA256 || n.RoutingEpoch != r.RoutingEpoch || n.PlanSHA256 != r.PlanSHA256 {
		return errHostPreflight
	}
	if r.Operation == "cold-b0-forward-plan" {
		if n.RetainedPhase != "reserved" {
			return errHostPreflight
		}
	} else {
		if prior.RoutingEpoch != r.RoutingEpoch || prior.PlanSHA256 != r.PlanSHA256 || prior.ForwardRequestSHA256 != r.ForwardRequestSHA256 || n.B0ForwardPlanSHA256 != r.ForwardPlanSHA256 || len(n.B0ForwardPlan) == 0 || hostDigest(n.B0ForwardPlan) != r.ForwardPlanSHA256 {
			return errHostPreflight
		}
	}
	if coldForwardPublicationOperation(r.Operation) && (n.B0ForwardPhase != "redis-transferred" || n.B0ForwardReceiptSHA256 != r.ForwardReceiptSHA256) {
		return errHostPreflight
	}
	return nil
}

func (s *hostColdPhaseScope) forwardPredecessor(r, prior HostColdPhaseRequest, parent *HostColdPhaseResult) error {
	if checkHostColdForwardLink(r, prior, parent) != nil {
		return errHostPreflight
	}
	// Inspection intentionally omits live Lua/target inputs. Walk the immutable
	// ancestry to recover the original intent/target binding, never a latest SQL
	// reservation or a caller's alternate plan. Bound retries and reject cycles.
	original := r
	seen := map[string]bool{}
	var target, lua string
	for depth := 0; ; depth++ {
		if depth >= 64 || seen[r.PredecessorSHA256] {
			return errHostPreflight
		}
		seen[r.PredecessorSHA256] = true
		p, result, err := s.loadPredecessor(r)
		if err != nil {
			return errHostPreflight
		}
		if result == nil {
			if r.Operation != "cold-b0-target" || target == "" || lua == "" || r.LuaSHA256 != lua {
				return errHostPreflight
			}
			break
		}
		if result.Outcome == "completed" {
			if coldB0ForwardOperation(r.Operation) {
				if checkHostColdForwardLink(r, *p, result) != nil {
					return errHostPreflight
				}
			} else if _, _, err := s.predecessor(r); err != nil {
				return errHostPreflight
			}
		}
		if r.Operation != "cold-b0-target" && r.IntentSHA256 != original.IntentSHA256 {
			return errHostPreflight
		}
		if r.Operation == "cold-begin" {
			if target != "" && target != r.TargetSHA256 || lua != "" && lua != r.LuaSHA256 {
				return errHostPreflight
			}
			target, lua = r.TargetSHA256, r.LuaSHA256
		}
		if r.TargetSHA256 != "" {
			if target != "" && target != r.TargetSHA256 {
				return errHostPreflight
			}
			target = r.TargetSHA256
		}
		if r.LuaSHA256 != "" {
			if lua != "" && lua != r.LuaSHA256 {
				return errHostPreflight
			}
			lua = r.LuaSHA256
		}
		r = *p
	}
	if original.Operation != "cold-b0-forward-inspect" && (original.TargetSHA256 != target || original.LuaSHA256 != lua) {
		return errHostPreflight
	}
	return nil
}

func validateHostColdForwardResult(r HostColdPhaseRequest, n *ColdAdminIdentity, parent *HostColdPhaseResult) error {
	if validateHostColdForwardRequest(r) != nil || n.IntentSHA256 != r.IntentSHA256 || n.RoutingEpoch != r.RoutingEpoch || n.PlanSHA256 != r.PlanSHA256 || n.B0TargetSHA256 != r.TargetSHA256 || !planPattern.MatchString(n.B0ForwardPlanSHA256) || len(n.B0ForwardPlan) == 0 || len(n.B0ForwardPlan) > 32<<20 || hostDigest(n.B0ForwardPlan) != n.B0ForwardPlanSHA256 {
		return errHostPreflight
	}
	if r.Operation != "cold-b0-forward-plan" && (n.B0ForwardPlanSHA256 != r.ForwardPlanSHA256 || parent == nil || parent.Native == nil || !bytes.Equal(n.B0ForwardPlan, parent.Native.B0ForwardPlan)) {
		return errHostPreflight
	}
	switch r.Operation {
	case "cold-b0-forward-plan", "cold-b0-forward-retain":
		if n.B0ForwardPhase != "" || n.B0ForwardReceiptSHA256 != "" {
			return errHostPreflight
		}
	default:
		if n.B0ForwardPhase != "redis-transferred" || !planPattern.MatchString(n.B0ForwardReceiptSHA256) || coldForwardPublicationOperation(r.Operation) && n.B0ForwardReceiptSHA256 != r.ForwardReceiptSHA256 {
			return errHostPreflight
		}
		if r.Operation == "cold-b0-forward-inspect" && (parent == nil || parent.Native == nil || n.B0ForwardReceiptSHA256 != parent.Native.B0ForwardReceiptSHA256) {
			return errHostPreflight
		}
	}
	return nil
}
