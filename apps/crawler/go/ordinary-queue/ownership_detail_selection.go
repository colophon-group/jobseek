package queue

type ownershipDetailSelection struct {
	details  []ownershipDetail
	bindings map[string]ownershipDetail
}

// Index only the immutable, fully decoded plan. Canonical configuration, SQL
// source locks, Redis due/throttle checks and the fenced pop remain live reads.
func (p *OwnershipPlan) indexDetailSelections() *OwnershipPlan {
	p.detailSelections = make(map[WorkerType]ownershipDetailSelection, 2)
	for _, worker := range []WorkerType{Simple, Browser} {
		details, bindings := p.buildDetailSelection(worker)
		p.detailSelections[worker] = ownershipDetailSelection{details, bindings}
	}
	return p
}

func (p *OwnershipPlan) detailSelection(worker WorkerType) ([]ownershipDetail, map[string]ownershipDetail) {
	if p.detailSelections != nil {
		selected := p.detailSelections[worker]
		return selected.details, selected.bindings
	}
	// Internal fixtures can construct documents directly; production plans
	// always pass through decodeOwnership before reaching this method.
	return p.buildDetailSelection(worker)
}

func (p *OwnershipPlan) buildDetailSelection(worker WorkerType) ([]ownershipDetail, map[string]ownershipDetail) {
	details := make([]ownershipDetail, 0, len(p.document.Details))
	for _, d := range p.document.Details {
		if d.Worker == worker {
			details = append(details, d)
		}
	}
	bindings := make(map[string]ownershipDetail, len(details))
	for _, d := range details {
		bindings[d.BoardID] = d
	}
	return details, bindings
}
