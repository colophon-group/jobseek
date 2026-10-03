package worker

import queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"

func hostColdReversalOperation(operation string) bool {
	return operation == "cold-reversal-begin" || operation == "cold-reversal-reserve" || operation == "cold-reversal-inspect"
}

func validateHostColdReversalRequest(r HostColdPhaseRequest) error {
	if !hostColdReversalOperation(r.Operation) || !hostColdRestorationInputsEmpty(r) || !planPattern.MatchString(r.PredecessorSHA256) || r.RoutingEpoch <= r.PreviousEpoch || r.RoutingEpoch >= 9999999999999 || !planPattern.MatchString(r.IntentSHA256) || !planPattern.MatchString(r.PlanSHA256) || !planPattern.MatchString(r.ReversalSHA256) || r.TargetSHA256 != "" || r.LuaSHA256 != "" || r.Namespace != "" || r.Shard != "" || r.Cohort != "" || r.ForwardRequestSHA256 != "" || r.ForwardPlanSHA256 != "" || r.ForwardReceiptSHA256 != "" {
		return errHostPreflight
	}
	if r.Operation == "cold-reversal-inspect" {
		if r.RetirementEpoch <= r.RoutingEpoch || r.RetirementEpoch > 9999999999999 {
			return errHostPreflight
		}
	} else if r.RetirementEpoch != 0 {
		return errHostPreflight
	}
	return nil
}

// A reversal branches only from completed native history. Unresolved effects
// still require their exact-input retry; reversal cannot silently adopt them.
func hostColdReversalSourcePhase(operation string) string {
	switch operation {
	case "cold-reserve", "cold-inspect", "cold-b0-forward-plan", "cold-b0-forward-retain", "cold-b0-forward-apply", "cold-b0-forward-inspect":
		return "reserved"
	case "cold-forward-prepare":
		return "publishing"
	case "cold-forward-publish":
		return "published"
	case "cold-forward-activate":
		return "active"
	}
	return ""
}

func checkHostColdReversalLink(r, prior HostColdPhaseRequest, parent *HostColdPhaseResult) error {
	if validateHostColdReversalRequest(r) != nil || parent == nil || parent.Outcome != "completed" || parent.Native == nil {
		return errHostPreflight
	}
	n := parent.Native
	if n.Version != "jobseek.ordinary.cold-identity/v1" || n.Operation != prior.Operation || n.SourceRevision != r.Binding.SourceRevision || n.IntentSHA256 != r.IntentSHA256 || prior.IntentSHA256 != r.IntentSHA256 || n.RoutingEpoch != r.RoutingEpoch || n.PlanSHA256 != r.PlanSHA256 {
		return errHostPreflight
	}
	if r.Operation == "cold-reversal-begin" {
		if hostColdReversalSourcePhase(prior.Operation) == "" || prior.Operation == "cold-inspect" && n.RetainedPhase != "reserved" {
			return errHostPreflight
		}
		if coldB0ForwardOperation(prior.Operation) && (prior.RoutingEpoch != r.RoutingEpoch || prior.PlanSHA256 != r.PlanSHA256) {
			return errHostPreflight
		}
		return nil
	}
	if prior.RoutingEpoch != r.RoutingEpoch || prior.PlanSHA256 != r.PlanSHA256 || prior.ReversalSHA256 != r.ReversalSHA256 || n.ReversalSHA256 != r.ReversalSHA256 {
		return errHostPreflight
	}
	if r.Operation == "cold-reversal-reserve" {
		if prior.Operation != "cold-reversal-begin" || n.RetirementEpoch != 0 || n.ReversalPhase != "" {
			return errHostPreflight
		}
	} else if prior.Operation != "cold-reversal-reserve" || n.RetirementEpoch != r.RetirementEpoch || n.ReversalPhase != "reserved" {
		return errHostPreflight
	}
	return nil
}

func (s *hostColdPhaseScope) reversalPredecessor(r, prior HostColdPhaseRequest, parent *HostColdPhaseResult) error {
	if checkHostColdReversalLink(r, prior, parent) != nil {
		return errHostPreflight
	}
	return s.retirementAncestry(r)
}

func (s *hostColdPhaseScope) retirementAncestry(r HostColdPhaseRequest) error {
	// Re-check each immutable ancestor, including the completed forward anchor
	// and original reservation. A retained JSON hash alone cannot grant a branch.
	seen := map[string]bool{}
	prior := r
	var target, lua string
	for depth := 0; ; depth++ {
		if depth >= 64 || seen[prior.PredecessorSHA256] {
			return errHostPreflight
		}
		seen[prior.PredecessorSHA256] = true
		if prior.TargetSHA256 != "" {
			if target != "" && target != prior.TargetSHA256 {
				return errHostPreflight
			}
			target = prior.TargetSHA256
		}
		if prior.LuaSHA256 != "" {
			if lua != "" && lua != prior.LuaSHA256 {
				return errHostPreflight
			}
			lua = prior.LuaSHA256
		}
		p, result, err := s.loadPredecessor(prior)
		if err != nil {
			return errHostPreflight
		}
		if result == nil {
			if prior.Operation != "cold-b0-target" || target == "" || lua == "" {
				return errHostPreflight
			}
			return nil
		}
		if prior.Operation != "cold-b0-target" && prior.IntentSHA256 != r.IntentSHA256 {
			return errHostPreflight
		}
		if result.Outcome == "completed" {
			if hostColdCompletionOperation(prior.Operation) {
				if checkHostColdCompletionLink(prior, *p, result) != nil {
					return errHostPreflight
				}
			} else if hostColdRestorationOperation(prior.Operation) {
				if checkHostColdRestorationLink(prior, *p, result) != nil {
					return errHostPreflight
				}
			} else if hostColdReversalOperation(prior.Operation) {
				if checkHostColdReversalLink(prior, *p, result) != nil {
					return errHostPreflight
				}
			} else if _, _, err := s.predecessor(prior); err != nil {
				return errHostPreflight
			}
		}
		prior = *p
	}
}

func (s *hostColdPhaseScope) checkReversalSpec(r HostColdPhaseRequest, intent queue.ColdTransitionSpec, inputs map[string][]byte) error {
	reversal, err := queue.DecodeColdReversalSpec(string(inputs["cold-input-"+r.ReversalSHA256]), r.ReversalSHA256)
	if err != nil || reversal.SourceRevision != r.Binding.SourceRevision || reversal.ForwardIntentSHA256 != r.IntentSHA256 || reversal.SourceEpoch != r.RoutingEpoch || reversal.SourcePlanSHA256 != r.PlanSHA256 || reversal.RollbackReleaseSHA256 != s.info.RollbackReleaseSHA256 || reversal.RollbackOrdinaryPlanSHA256 != intent.PreviousOrdinaryPlanSHA256 || reversal.RollbackB0ReceiptSHA256 != intent.PreviousB0ReceiptSHA256 || reversal.ColdAttestationSHA256 != s.info.ColdAttestationSHA256 {
		return errHostPreflight
	}
	// Recover the original completed branch across separate unresolved retries
	// and reserve/inspect events. Never use the allocator's current high water.
	for depth := 0; depth < 64; depth++ {
		prior, parent, err := s.loadPredecessor(r)
		if err != nil || prior == nil || parent == nil {
			return errHostPreflight
		}
		if !hostColdRetirementOperation(prior.Operation) {
			if parent.Outcome != "completed" || reversal.SourcePhase != hostColdReversalSourcePhase(prior.Operation) {
				return errHostPreflight
			}
			return nil
		}
		if prior.ReversalSHA256 != r.ReversalSHA256 {
			return errHostPreflight
		}
		r = *prior
	}
	return errHostPreflight
}

func validateHostColdReversalResult(r HostColdPhaseRequest, n *ColdAdminIdentity, parent *HostColdPhaseResult) error {
	if validateHostColdReversalRequest(r) != nil || n.IntentSHA256 != r.IntentSHA256 || n.RoutingEpoch != r.RoutingEpoch || n.PlanSHA256 != r.PlanSHA256 || n.ReversalSHA256 != r.ReversalSHA256 || n.B0TargetSHA256 != "" {
		return errHostPreflight
	}
	if r.Operation == "cold-reversal-begin" {
		if n.RetirementEpoch != 0 || n.ReversalPhase != "" {
			return errHostPreflight
		}
	} else if n.RetirementEpoch <= r.RoutingEpoch || n.RetirementEpoch > 9999999999999 || n.ReversalPhase != "reserved" || r.Operation == "cold-reversal-inspect" && (n.RetirementEpoch != r.RetirementEpoch || parent == nil || parent.Native == nil || parent.Native.RetirementEpoch != r.RetirementEpoch) {
		return errHostPreflight
	}
	return nil
}
