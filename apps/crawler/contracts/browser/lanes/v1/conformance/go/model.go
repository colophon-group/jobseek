// Package browserlanesv1 implements the inert, offline browser-lanes v1
// conformance model. It has no crawler, queue, browser, clock, or network I/O.
package browserlanesv1

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

const (
	Format        = "jobseek.browser-lanes.v1.conformance/v1"
	maxDocument   = 1_048_576
	maxDepth      = 12
	maxArray      = 4096
	maxObject     = 64
	maxString     = 4096
	maxCases      = 512
	maxItems      = 4096
	maxInteger    = 9_007_199_254_740_991
	maxConcurrent = 4096
	telemetryAge  = 30
	proofAge      = 30
	proofWindow   = 900
	drainWindow   = 30
	cooldown      = 60
	ageOverride   = 900
)

var (
	errInvalidInput = errors.New("invalid_input")
	safeID          = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$`)
	privatePattern  = regexp.MustCompile(`(?i)(?:\d{1,3}\.){3}\d{1,3}|\[[0-9a-f:]+\]|(?:[a-z0-9-]+\.)+[a-z][a-z0-9-]*`)
)

var laneNames = [...]string{"lightpanda", "chromium"}

var reasonRegistry = map[string]bool{
	"assignment_invalid": true, "assignment_lane_mismatch": true,
	"assignment_mutated": true, "capacity_headroom_unsafe": true,
	"conservation_failure": true, "drain_active": true,
	"error_budget_exhausted": true, "fallback_attempted": true,
	"hard_max_reached": true, "invalid_input": true,
	"no_eligible_backlog": true, "policy_violation": true,
	"queue_fence_invalid": true, "resource_saturation": true,
	"revision_mismatch": true, "scale_cooldown_active": true,
	"scale_up_requested": true, "service_error": true,
	"service_full": true, "service_unready": true,
	"service_unsupported": true, "telemetry_stale": true,
	"zero_proof_absent": true, "zero_proof_demand_present": true,
	"zero_proof_invalid": true, "zero_proof_revision_mismatch": true,
	"zero_proof_stale": true,
}

// Corpus preserves each raw case input, including the one intentionally
// malformed semantic input whose normalized result is invalid_input.
type Corpus struct {
	Cases  []Case `json:"cases"`
	Format string `json:"format"`
}

type Case struct {
	Expected     json.RawMessage `json:"expected"`
	ID           string          `json:"id"`
	Input        json.RawMessage `json:"input"`
	ResultDigest string          `json:"result_digest"`
}

type Input struct {
	CapabilityCensusRevision string              `json:"capability_census_revision"`
	ConfigRevision           string              `json:"config_revision"`
	DeclaredAssignmentCount  uint64              `json:"declared_assignment_count"`
	InvalidationEvents       []InvalidationEvent `json:"invalidation_events"`
	Lanes                    []Lane              `json:"lanes"`
	Now                      uint64              `json:"now"`
	Placements               Placements          `json:"placements"`
	PolicyRevision           string              `json:"policy_revision"`
	QueueRevision            string              `json:"queue_revision"`
	RoutingRevision          string              `json:"routing_revision"`
}

type Placements struct {
	Inflight []Placement `json:"inflight"`
	Ready    []Placement `json:"ready"`
}

type Placement struct {
	Admission      Admission  `json:"admission"`
	Assignment     Assignment `json:"assignment"`
	DueAt          uint64     `json:"due_at"`
	EligibleSince  uint64     `json:"eligible_since"`
	FallbackTarget string     `json:"fallback_target"`
	Fence          Fence      `json:"fence"`
	Lane           string     `json:"lane"`
	Ordinal        uint64     `json:"ordinal"`
	Priority       string     `json:"priority"`
	WorkClass      string     `json:"work_class"`
}

type Admission struct {
	PolicyRevision string `json:"policy_revision"`
	Verdict        string `json:"verdict"`
}

type Assignment struct {
	Backend         string         `json:"backend"`
	CapabilityClass string         `json:"capability_class"`
	ImmutableCopy   AssignmentCopy `json:"immutable_copy"`
	RoutingRevision string         `json:"routing_revision"`
	ServiceLane     string         `json:"service_lane"`
}

type AssignmentCopy struct {
	Backend         string `json:"backend"`
	CapabilityClass string `json:"capability_class"`
	RoutingRevision string `json:"routing_revision"`
	ServiceLane     string `json:"service_lane"`
}

type Fence struct {
	ClaimFence     uint64 `json:"claim_fence"`
	ConfigRevision string `json:"config_revision"`
	EngineOwner    string `json:"engine_owner"`
	QueueRevision  string `json:"queue_revision"`
	RoutingEpoch   uint64 `json:"routing_epoch"`
	ShardID        string `json:"shard_id"`
}

type Lane struct {
	Capacity     Capacity   `json:"capacity"`
	Declared     Declared   `json:"declared"`
	Lane         string     `json:"lane"`
	QueueFence   Fence      `json:"queue_fence"`
	ServiceState string     `json:"service_state"`
	Telemetry    Telemetry  `json:"telemetry"`
	ZeroProof    *ZeroProof `json:"zero_proof"`
}

type Capacity struct {
	Admitted       uint64 `json:"admitted"`
	Current        uint64 `json:"current"`
	Desired        uint64 `json:"desired"`
	DrainStartedAt uint64 `json:"drain_started_at"`
	Draining       bool   `json:"draining"`
	HardMax        uint64 `json:"hard_max"`
	Inflight       uint64 `json:"inflight"`
	LastScaleAt    uint64 `json:"last_scale_at"`
	Running        uint64 `json:"running"`
	ScaleDownStep  uint64 `json:"scale_down_step"`
	ScaleUpStep    uint64 `json:"scale_up_step"`
	WarmFloor      uint64 `json:"warm_floor"`
}

type Declared struct {
	AssignmentCount    uint64 `json:"assignment_count"`
	EligibleReadyCount uint64 `json:"eligible_ready_count"`
	InflightCount      uint64 `json:"inflight_count"`
	OldestEligibleAge  uint64 `json:"oldest_eligible_age"`
	ReadyCount         uint64 `json:"ready_count"`
}

type Telemetry struct {
	ErrorBudgetBurn     float64 `json:"error_budget_burn"`
	HeadroomP05Ratio    float64 `json:"headroom_p05_ratio"`
	ObservedAt          uint64  `json:"observed_at"`
	QueueOldestAge      uint64  `json:"queue_oldest_age"`
	ResourceSaturated   bool    `json:"resource_saturated"`
	UtilizationP95Ratio float64 `json:"utilization_p95_ratio"`
}

type ZeroProof struct {
	AssignmentCount          uint64  `json:"assignment_count"`
	CapabilityCensusRevision string  `json:"capability_census_revision"`
	Complete                 bool    `json:"complete"`
	CompletedAt              uint64  `json:"completed_at"`
	ConfigRevision           string  `json:"config_revision"`
	EligibleReadyCount       uint64  `json:"eligible_ready_count"`
	InflightCount            uint64  `json:"inflight_count"`
	OldestEligibleSince      *uint64 `json:"oldest_eligible_since"`
	PolicyRevision           string  `json:"policy_revision"`
	QueueFence               Fence   `json:"queue_fence"`
	QueueRevision            string  `json:"queue_revision"`
	ReadyCount               uint64  `json:"ready_count"`
	RoutingRevision          string  `json:"routing_revision"`
	StartedAt                uint64  `json:"started_at"`
}

type InvalidationEvent struct {
	CapabilityCensusRevision string `json:"capability_census_revision"`
	ConfigRevision           string `json:"config_revision"`
	EventAt                  uint64 `json:"event_at"`
	EventOrdinal             uint64 `json:"event_ordinal"`
	Kind                     string `json:"kind"`
	Lane                     string `json:"lane"`
	PolicyRevision           string `json:"policy_revision"`
	QueueRevision            string `json:"queue_revision"`
	RoutingRevision          string `json:"routing_revision"`
	WorkOrdinal              uint64 `json:"work_ordinal"`
}

type Result struct {
	Lanes map[string]Decision `json:"lanes"`
}

type Decision struct {
	Decision           string   `json:"decision"`
	DesiredConcurrency uint64   `json:"desired_concurrency"`
	Lane               string   `json:"lane"`
	Reasons            []string `json:"reasons"`
	SelectedItemIndex  *uint64  `json:"selected_item_index"`
}

type laneFacts struct {
	AssignmentCount    uint64
	EligibleReadyCount uint64
	InflightCount      uint64
	OldestEligibleAge  uint64
	OldestSince        *uint64
	ReadyCount         uint64
}

type fenceIdentity struct {
	QueueRevision  string
	ShardID        string
	RoutingEpoch   uint64
	EngineOwner    string
	ConfigRevision string
	ClaimFence     uint64
}

func DecodeCorpus(data []byte) (Corpus, error) {
	value, err := strictParse(data, true)
	if err != nil {
		return Corpus{}, errInvalidInput
	}
	root, ok := asObject(value)
	if !ok || !exactKeys(root, "cases", "format") {
		return Corpus{}, errInvalidInput
	}
	cases, ok := root["cases"].([]any)
	if !ok || len(cases) > maxCases {
		return Corpus{}, errInvalidInput
	}
	var corpus Corpus
	if decodeExact(data, &corpus) != nil || corpus.Format != Format || len(corpus.Cases) != len(cases) {
		return Corpus{}, errInvalidInput
	}
	seen := make(map[string]bool, len(corpus.Cases))
	for index, rawCase := range cases {
		object, ok := asObject(rawCase)
		if !ok || !exactKeys(object, "expected", "id", "input", "result_digest") {
			return Corpus{}, errInvalidInput
		}
		testCase := corpus.Cases[index]
		if !validID(testCase.ID) || seen[testCase.ID] || !isDigest(testCase.ResultDigest) {
			return Corpus{}, errInvalidInput
		}
		seen[testCase.ID] = true
		actual := EvaluateDocument(testCase.Input)
		if !bytes.Equal(actual, testCase.Expected) || digestBytes(actual) != testCase.ResultDigest {
			return Corpus{}, errInvalidInput
		}
	}
	return corpus, nil
}

func DecodeInput(data []byte) (Input, error) {
	value, err := strictParse(data, false)
	if err != nil || !validInputShape(value) {
		return Input{}, errInvalidInput
	}
	var input Input
	if decodeExact(data, &input) != nil || validateInput(input) != nil {
		return Input{}, errInvalidInput
	}
	return input, nil
}

func EvaluateDocument(data []byte) []byte {
	input, err := DecodeInput(data)
	if err != nil {
		return []byte(`{"error":"invalid_input"}`)
	}
	result, err := CanonicalBytes(Evaluate(input))
	if err != nil {
		return []byte(`{"error":"invalid_input"}`)
	}
	return result
}

func RunCase(testCase Case) ([]byte, error) {
	if !validID(testCase.ID) || !isDigest(testCase.ResultDigest) {
		return nil, errInvalidInput
	}
	result := EvaluateDocument(testCase.Input)
	if !bytes.Equal(result, testCase.Expected) || digestBytes(result) != testCase.ResultDigest {
		return nil, errInvalidInput
	}
	return result, nil
}

func VerifyCorpusSidecar(corpus, sidecar []byte) error {
	if len(sidecar) != 65 || sidecar[64] != '\n' || !isDigest(string(sidecar[:64])) {
		return errInvalidInput
	}
	sum := sha256.Sum256(corpus)
	if !bytes.Equal(sidecar[:64], []byte(hex.EncodeToString(sum[:]))) {
		return errInvalidInput
	}
	return nil
}

func Evaluate(input Input) Result {
	if validateInput(input) != nil {
		return Result{}
	}
	freeze, eligible, facts := globalAudit(input)
	decisions := make(map[string]Decision, len(laneNames))
	for index, name := range laneNames {
		decisions[name] = evaluateLane(input, input.Lanes[index], freeze[name], eligible[name], facts[name])
	}
	return Result{Lanes: decisions}
}

func globalAudit(input Input) (map[string]map[string]bool, map[string][]Placement, map[string]laneFacts) {
	freeze := map[string]map[string]bool{"lightpanda": {}, "chromium": {}}
	eligible := map[string][]Placement{"lightpanda": {}, "chromium": {}}
	facts := make(map[string]laneFacts, len(laneNames))
	lanes := map[string]Lane{"lightpanda": input.Lanes[0], "chromium": input.Lanes[1]}
	combined := append(append([]Placement{}, input.Placements.Ready...), input.Placements.Inflight...)
	counts := make(map[uint64]int, len(combined))
	seenOrdinals := make(map[uint64]bool, len(combined))
	for _, placement := range combined {
		counts[placement.Ordinal]++
		seenOrdinals[placement.Ordinal] = true
	}
	globalFailure := len(combined) != int(input.DeclaredAssignmentCount) || len(seenOrdinals) != int(input.DeclaredAssignmentCount)
	if !globalFailure {
		for ordinal := uint64(0); ordinal < input.DeclaredAssignmentCount; ordinal++ {
			if !seenOrdinals[ordinal] {
				globalFailure = true
				break
			}
		}
	}
	if globalFailure {
		addAll(freeze, "conservation_failure")
	}
	for ordinal, count := range counts {
		if count > 1 {
			for _, placement := range combined {
				if placement.Ordinal == ordinal {
					freeze[placement.Lane]["conservation_failure"] = true
				}
			}
		}
	}
	readyOrdinals := make(map[uint64]bool, len(input.Placements.Ready))
	for _, placement := range input.Placements.Ready {
		readyOrdinals[placement.Ordinal] = true
	}
	for _, placement := range input.Placements.Inflight {
		if readyOrdinals[placement.Ordinal] {
			freeze[placement.Lane]["conservation_failure"] = true
			for _, ready := range input.Placements.Ready {
				if ready.Ordinal == placement.Ordinal {
					freeze[ready.Lane]["conservation_failure"] = true
				}
			}
		}
	}
	fenceOwners := make(map[fenceIdentity]map[string]bool, len(laneNames))
	for _, name := range laneNames {
		lane := lanes[name]
		identity := fenceID(lane.QueueFence)
		if fenceOwners[identity] == nil {
			fenceOwners[identity] = map[string]bool{}
		}
		fenceOwners[identity][name] = true
		if lane.QueueFence.QueueRevision != input.QueueRevision || lane.QueueFence.ConfigRevision != input.ConfigRevision {
			freeze[name]["queue_fence_invalid"] = true
		}
	}
	for _, owners := range fenceOwners {
		if len(owners) > 1 {
			for name := range owners {
				freeze[name]["conservation_failure"] = true
			}
		}
	}
	for _, placement := range combined {
		failures := placementFailures(input, lanes[placement.Lane], placement)
		implicated := implicatedLanes(placement)
		for reason := range failures {
			for name := range implicated {
				freeze[name][reason] = true
			}
		}
		owners := fenceOwners[fenceID(placement.Fence)]
		if len(owners) > 0 && !(len(owners) == 1 && owners[placement.Lane]) {
			freeze[placement.Lane]["conservation_failure"] = true
			freeze[placement.Lane]["queue_fence_invalid"] = true
			for name := range owners {
				freeze[name]["conservation_failure"] = true
				freeze[name]["queue_fence_invalid"] = true
			}
		}
	}
	var assignmentTotal uint64
	for _, name := range laneNames {
		lane := lanes[name]
		var laneReady, laneInflight []Placement
		for _, placement := range input.Placements.Ready {
			if placement.Lane == name {
				laneReady = append(laneReady, placement)
			}
		}
		for _, placement := range input.Placements.Inflight {
			if placement.Lane == name {
				laneInflight = append(laneInflight, placement)
			}
		}
		var oldestSince *uint64
		for _, placement := range laneReady {
			if placement.DueAt <= input.Now && placement.Admission.Verdict == "permit" {
				eligible[name] = append(eligible[name], placement)
				if oldestSince == nil || placement.EligibleSince < *oldestSince {
					value := placement.EligibleSince
					oldestSince = &value
				}
			}
		}
		fact := laneFacts{
			AssignmentCount:    uint64(len(laneReady) + len(laneInflight)),
			EligibleReadyCount: uint64(len(eligible[name])),
			InflightCount:      uint64(len(laneInflight)),
			OldestSince:        oldestSince,
			ReadyCount:         uint64(len(laneReady)),
		}
		if oldestSince != nil {
			fact.OldestEligibleAge = input.Now - *oldestSince
		}
		facts[name] = fact
		assignmentTotal += fact.AssignmentCount
		if lane.Declared.AssignmentCount != fact.AssignmentCount || lane.Declared.EligibleReadyCount != fact.EligibleReadyCount || lane.Declared.InflightCount != fact.InflightCount || lane.Declared.OldestEligibleAge != fact.OldestEligibleAge || lane.Declared.ReadyCount != fact.ReadyCount || lane.Capacity.Inflight != fact.InflightCount {
			freeze[name]["conservation_failure"] = true
		}
	}
	if assignmentTotal != input.DeclaredAssignmentCount {
		addAll(freeze, "conservation_failure")
	}
	return freeze, eligible, facts
}

func placementFailures(input Input, lane Lane, placement Placement) map[string]bool {
	failures := map[string]bool{}
	a := placement.Assignment
	c := a.ImmutableCopy
	if a.Backend != a.ServiceLane || c.Backend != c.ServiceLane || a.CapabilityClass != "browser-default" || c.CapabilityClass != "browser-default" {
		failures["assignment_invalid"] = true
	}
	if a.Backend != c.Backend || a.CapabilityClass != c.CapabilityClass || a.RoutingRevision != c.RoutingRevision || a.ServiceLane != c.ServiceLane {
		failures["assignment_mutated"] = true
	}
	if a.Backend != placement.Lane || a.ServiceLane != placement.Lane || c.Backend != placement.Lane || c.ServiceLane != placement.Lane {
		failures["assignment_lane_mismatch"] = true
	}
	if a.RoutingRevision != input.RoutingRevision || c.RoutingRevision != input.RoutingRevision {
		failures["revision_mismatch"] = true
	}
	if placement.Fence != lane.QueueFence || placement.Fence.QueueRevision != input.QueueRevision || placement.Fence.ConfigRevision != input.ConfigRevision {
		failures["queue_fence_invalid"] = true
	}
	if placement.Admission.PolicyRevision != input.PolicyRevision || placement.Admission.Verdict == "violation" {
		failures["policy_violation"] = true
	}
	if placement.FallbackTarget != "none" {
		failures["fallback_attempted"] = true
	}
	return failures
}

func implicatedLanes(placement Placement) map[string]bool {
	result := map[string]bool{
		placement.Lane: true, placement.Assignment.Backend: true,
		placement.Assignment.ServiceLane:               true,
		placement.Assignment.ImmutableCopy.Backend:     true,
		placement.Assignment.ImmutableCopy.ServiceLane: true,
	}
	if placement.FallbackTarget != "none" {
		result[placement.FallbackTarget] = true
	}
	return result
}

func evaluateLane(input Input, lane Lane, freezeReasons map[string]bool, eligible []Placement, facts laneFacts) Decision {
	base, valid := capacityBase(lane.Capacity)
	if !valid {
		freezeReasons["invalid_input"] = true
		base = max(lane.Capacity.Inflight, lane.Capacity.WarmFloor)
	}
	if reason := map[string]string{"unready": "service_unready", "error": "service_error", "unsupported": "service_unsupported", "full": "service_full"}[lane.ServiceState]; reason != "" {
		freezeReasons[reason] = true
	}
	telemetry := lane.Telemetry
	if telemetry.ObservedAt > input.Now || input.Now-telemetry.ObservedAt > telemetryAge {
		freezeReasons["telemetry_stale"] = true
	}
	if telemetry.ErrorBudgetBurn > 1 {
		freezeReasons["error_budget_exhausted"] = true
	}
	if telemetry.ResourceSaturated {
		freezeReasons["resource_saturation"] = true
	}
	if len(freezeReasons) > 0 {
		return decision(lane.Lane, "freeze", base, freezeReasons, nil)
	}
	deferReasons := map[string]bool{}
	if telemetry.UtilizationP95Ratio > .85 || telemetry.HeadroomP05Ratio < .15 {
		deferReasons["capacity_headroom_unsafe"] = true
	}
	draining := lane.Capacity.Draining && lane.Capacity.DrainStartedAt > 0 && activeWindow(input.Now, lane.Capacity.DrainStartedAt, drainWindow)
	cooling := lane.Capacity.LastScaleAt != 0 && activeWindow(input.Now, lane.Capacity.LastScaleAt, cooldown)
	if len(eligible) > 0 {
		if lane.Capacity.Admitted > lane.Capacity.Inflight && len(deferReasons) == 0 {
			selected := choose(input.Now, eligible)
			return decision(lane.Lane, "claim", base, nil, &selected.Ordinal)
		}
		if draining {
			deferReasons["drain_active"] = true
		}
		if cooling {
			deferReasons["scale_cooldown_active"] = true
		}
		if base >= lane.Capacity.HardMax {
			deferReasons["hard_max_reached"] = true
		}
		if len(deferReasons) == 0 {
			deferReasons["scale_up_requested"] = true
			base = min(lane.Capacity.HardMax, base+lane.Capacity.ScaleUpStep)
		}
		return decision(lane.Lane, "defer", base, deferReasons, nil)
	}
	deferReasons["no_eligible_backlog"] = true
	proofFailure := proofReason(input, lane, facts)
	if proofFailure != "" {
		deferReasons[proofFailure] = true
	}
	if draining {
		deferReasons["drain_active"] = true
	}
	if cooling {
		deferReasons["scale_cooldown_active"] = true
	}
	if proofFailure == "" && !draining && !cooling && lane.Capacity.Inflight == 0 && lane.Capacity.Running == 0 && lane.Capacity.Admitted == 0 && lane.Capacity.Current == 0 {
		base = 0
	}
	return decision(lane.Lane, "defer", base, deferReasons, nil)
}

func proofReason(input Input, lane Lane, facts laneFacts) string {
	proof := lane.ZeroProof
	if proof == nil {
		return "zero_proof_absent"
	}
	if proof.CapabilityCensusRevision != input.CapabilityCensusRevision || proof.ConfigRevision != input.ConfigRevision || proof.PolicyRevision != input.PolicyRevision || proof.QueueRevision != input.QueueRevision || proof.RoutingRevision != input.RoutingRevision || proof.QueueFence != lane.QueueFence {
		return "zero_proof_revision_mismatch"
	}
	if !proof.Complete || proof.CompletedAt < proof.StartedAt || proof.CompletedAt > input.Now || proof.CompletedAt-proof.StartedAt < proofWindow {
		return "zero_proof_invalid"
	}
	if input.Now-proof.CompletedAt > proofAge {
		return "zero_proof_stale"
	}
	if proof.AssignmentCount != facts.AssignmentCount || proof.EligibleReadyCount != facts.EligibleReadyCount || proof.InflightCount != facts.InflightCount || proof.ReadyCount != facts.ReadyCount || !sameOptional(proof.OldestEligibleSince, facts.OldestSince) {
		return "zero_proof_invalid"
	}
	if proof.AssignmentCount != 0 || proof.EligibleReadyCount != 0 || proof.InflightCount != 0 || proof.ReadyCount != 0 || proof.OldestEligibleSince != nil {
		return "zero_proof_demand_present"
	}
	for _, event := range input.InvalidationEvents {
		if event.Lane == lane.Lane && event.EventAt >= proof.CompletedAt && event.CapabilityCensusRevision == input.CapabilityCensusRevision && event.ConfigRevision == input.ConfigRevision && event.PolicyRevision == input.PolicyRevision && event.QueueRevision == input.QueueRevision && event.RoutingRevision == input.RoutingRevision {
			return "zero_proof_invalid"
		}
	}
	return ""
}

func choose(now uint64, placements []Placement) Placement {
	items := append([]Placement{}, placements...)
	hasOverride := false
	for _, item := range items {
		if now-item.EligibleSince >= ageOverride {
			hasOverride = true
			break
		}
	}
	if hasOverride {
		filtered := items[:0]
		for _, item := range items {
			if now-item.EligibleSince >= ageOverride {
				filtered = append(filtered, item)
			}
		}
		items = filtered
	}
	sort.Slice(items, func(i, j int) bool {
		a, b := items[i], items[j]
		if hasOverride {
			if a.EligibleSince != b.EligibleSince {
				return a.EligibleSince < b.EligibleSince
			}
		} else {
			scoreA := now - a.EligibleSince + priorityCredit(a.Priority)
			scoreB := now - b.EligibleSince + priorityCredit(b.Priority)
			if scoreA != scoreB {
				return scoreA > scoreB
			}
			if a.EligibleSince != b.EligibleSince {
				return a.EligibleSince < b.EligibleSince
			}
		}
		if priorityRank(a.Priority) != priorityRank(b.Priority) {
			return priorityRank(a.Priority) < priorityRank(b.Priority)
		}
		return a.Ordinal < b.Ordinal
	})
	return items[0]
}

func decision(lane, outcome string, desired uint64, reasonSet map[string]bool, selected *uint64) Decision {
	reasons := make([]string, 0, len(reasonSet))
	for reason := range reasonSet {
		if reasonRegistry[reason] {
			reasons = append(reasons, reason)
		}
	}
	sort.Strings(reasons)
	if outcome == "claim" {
		reasons = []string{}
	}
	return Decision{Decision: outcome, DesiredConcurrency: desired, Lane: lane, Reasons: reasons, SelectedItemIndex: selected}
}

func capacityBase(capacity Capacity) (uint64, bool) {
	if !(capacity.Inflight <= capacity.Running && capacity.Running <= capacity.Admitted && capacity.Admitted <= capacity.Current && capacity.Current <= capacity.HardMax && capacity.HardMax <= maxConcurrent && capacity.WarmFloor <= capacity.HardMax && capacity.Desired <= capacity.HardMax) {
		return 0, false
	}
	return min(max(capacity.Current, max(capacity.Inflight, capacity.WarmFloor)), capacity.HardMax), true
}

func CanonicalBytes(result Result) ([]byte, error) {
	if validateResult(result) != nil {
		return nil, errInvalidInput
	}
	data, err := json.Marshal(result)
	if err != nil {
		return nil, errInvalidInput
	}
	value, err := decodeAny(data)
	if err != nil {
		return nil, errInvalidInput
	}
	return canonicalValue(value)
}

func Digest(result Result) (string, error) {
	data, err := CanonicalBytes(result)
	if err != nil {
		return "", err
	}
	return digestBytes(data), nil
}

func digestBytes(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func validateInput(input Input) error {
	if input.Now > maxInteger || input.DeclaredAssignmentCount > maxItems || len(input.Placements.Ready)+len(input.Placements.Inflight) > maxItems || len(input.Lanes) != len(laneNames) || len(input.InvalidationEvents) > maxItems || !validID(input.CapabilityCensusRevision) || !validID(input.ConfigRevision) || !validID(input.PolicyRevision) || !validID(input.QueueRevision) || !validID(input.RoutingRevision) {
		return errInvalidInput
	}
	for _, placements := range [][]Placement{input.Placements.Ready, input.Placements.Inflight} {
		var previous uint64
		for index, placement := range placements {
			if validatePlacement(input, placement) != nil || index > 0 && placement.Ordinal < previous {
				return errInvalidInput
			}
			previous = placement.Ordinal
		}
	}
	for index, name := range laneNames {
		if validateLane(input.Lanes[index], name) != nil {
			return errInvalidInput
		}
	}
	for index, event := range input.InvalidationEvents {
		if event.EventOrdinal != uint64(index) || event.EventOrdinal >= maxItems || event.WorkOrdinal >= maxItems || event.EventAt > input.Now || (event.Kind != "assignment_created" && event.Kind != "became_eligible") || !validLane(event.Lane) || !validID(event.CapabilityCensusRevision) || !validID(event.ConfigRevision) || !validID(event.PolicyRevision) || !validID(event.QueueRevision) || !validID(event.RoutingRevision) {
			return errInvalidInput
		}
	}
	return nil
}

func validatePlacement(input Input, placement Placement) error {
	if placement.Ordinal >= maxItems || !validLane(placement.Lane) || !validPriority(placement.Priority) || (placement.WorkClass != "monitor" && placement.WorkClass != "detail") || (placement.FallbackTarget != "none" && !validLane(placement.FallbackTarget)) || placement.DueAt > input.Now || placement.EligibleSince > input.Now || !validAssignment(placement.Assignment) || !validFence(placement.Fence) || !validID(placement.Admission.PolicyRevision) || !validVerdict(placement.Admission.Verdict) {
		return errInvalidInput
	}
	return nil
}

func validAssignment(assignment Assignment) bool {
	return validLane(assignment.Backend) && validID(assignment.CapabilityClass) && validID(assignment.RoutingRevision) && validLane(assignment.ServiceLane) && validLane(assignment.ImmutableCopy.Backend) && validID(assignment.ImmutableCopy.CapabilityClass) && validID(assignment.ImmutableCopy.RoutingRevision) && validLane(assignment.ImmutableCopy.ServiceLane)
}

func validateLane(lane Lane, name string) error {
	capacity := lane.Capacity
	if lane.Lane != name || !validFence(lane.QueueFence) || !validServiceState(lane.ServiceState) || !validTelemetry(lane.Telemetry) || capacity.Admitted > maxConcurrent || capacity.Current > maxConcurrent || capacity.Desired > maxConcurrent || capacity.HardMax > maxConcurrent || capacity.Inflight > maxConcurrent || capacity.Running > maxConcurrent || capacity.ScaleDownStep == 0 || capacity.ScaleDownStep > maxConcurrent || capacity.ScaleUpStep == 0 || capacity.ScaleUpStep > maxConcurrent || capacity.WarmFloor > maxConcurrent || lane.Declared.AssignmentCount > maxItems || lane.Declared.EligibleReadyCount > maxItems || lane.Declared.InflightCount > maxItems || lane.Declared.ReadyCount > maxItems {
		return errInvalidInput
	}
	if lane.ZeroProof != nil && !validProof(lane.ZeroProof) {
		return errInvalidInput
	}
	return nil
}

func validProof(proof *ZeroProof) bool {
	return proof.AssignmentCount <= maxItems && proof.EligibleReadyCount <= maxItems && proof.InflightCount <= maxItems && proof.ReadyCount <= maxItems && validID(proof.CapabilityCensusRevision) && validID(proof.ConfigRevision) && validID(proof.PolicyRevision) && validID(proof.QueueRevision) && validID(proof.RoutingRevision) && validFence(proof.QueueFence) && (proof.OldestEligibleSince == nil || *proof.OldestEligibleSince <= maxInteger)
}

func validFence(fence Fence) bool {
	return fence.ClaimFence <= maxInteger && fence.RoutingEpoch <= maxInteger && validID(fence.ConfigRevision) && validID(fence.EngineOwner) && validID(fence.QueueRevision) && validID(fence.ShardID)
}

func validTelemetry(telemetry Telemetry) bool {
	return telemetry.ObservedAt <= maxInteger && telemetry.QueueOldestAge <= maxInteger && validRatio(telemetry.HeadroomP05Ratio) && validRatio(telemetry.UtilizationP95Ratio) && !math.IsNaN(telemetry.ErrorBudgetBurn) && !math.IsInf(telemetry.ErrorBudgetBurn, 0) && telemetry.ErrorBudgetBurn >= 0 && telemetry.ErrorBudgetBurn <= maxInteger
}

func validateResult(result Result) error {
	if len(result.Lanes) != len(laneNames) {
		return errInvalidInput
	}
	for _, name := range laneNames {
		value, ok := result.Lanes[name]
		if !ok || value.Lane != name || (value.Decision != "claim" && value.Decision != "defer" && value.Decision != "freeze") || value.DesiredConcurrency > maxConcurrent || !sort.StringsAreSorted(value.Reasons) || len(value.Reasons) > len(reasonRegistry) {
			return errInvalidInput
		}
		for index, reason := range value.Reasons {
			if !reasonRegistry[reason] || index > 0 && value.Reasons[index-1] == reason {
				return errInvalidInput
			}
		}
		if value.Decision == "claim" {
			if value.SelectedItemIndex == nil || len(value.Reasons) != 0 || *value.SelectedItemIndex >= maxItems {
				return errInvalidInput
			}
		} else if value.SelectedItemIndex != nil {
			return errInvalidInput
		}
	}
	return nil
}

func validInputShape(value any) bool {
	root, ok := asObject(value)
	if !ok || !exactKeys(root, "capability_census_revision", "config_revision", "declared_assignment_count", "invalidation_events", "lanes", "now", "placements", "policy_revision", "queue_revision", "routing_revision") || !typedFields(root, []string{"capability_census_revision", "config_revision", "policy_revision", "queue_revision", "routing_revision"}, []string{"declared_assignment_count", "now"}, nil) {
		return false
	}
	placements, ok := asObject(root["placements"])
	if !ok || !exactKeys(placements, "inflight", "ready") {
		return false
	}
	for _, key := range []string{"inflight", "ready"} {
		array, ok := placements[key].([]any)
		if !ok {
			return false
		}
		for _, raw := range array {
			if !validPlacementShape(raw) {
				return false
			}
		}
	}
	lanes, ok := root["lanes"].([]any)
	if !ok || len(lanes) != len(laneNames) {
		return false
	}
	for _, raw := range lanes {
		if !validLaneShape(raw) {
			return false
		}
	}
	events, ok := root["invalidation_events"].([]any)
	if !ok {
		return false
	}
	for _, raw := range events {
		if !validEventShape(raw) {
			return false
		}
	}
	return true
}

func validPlacementShape(value any) bool {
	object, ok := asObject(value)
	if !ok || !exactKeys(object, "admission", "assignment", "due_at", "eligible_since", "fallback_target", "fence", "lane", "ordinal", "priority", "work_class") || !typedFields(object, []string{"fallback_target", "lane", "priority", "work_class"}, []string{"due_at", "eligible_since", "ordinal"}, nil) {
		return false
	}
	admission, ok := asObject(object["admission"])
	if !ok || !exactKeys(admission, "policy_revision", "verdict") || !typedFields(admission, []string{"policy_revision", "verdict"}, nil, nil) {
		return false
	}
	assignment, ok := asObject(object["assignment"])
	if !ok || !exactKeys(assignment, "backend", "capability_class", "immutable_copy", "routing_revision", "service_lane") || !typedFields(assignment, []string{"backend", "capability_class", "routing_revision", "service_lane"}, nil, nil) {
		return false
	}
	immutable, ok := asObject(assignment["immutable_copy"])
	return ok && exactKeys(immutable, "backend", "capability_class", "routing_revision", "service_lane") && typedFields(immutable, []string{"backend", "capability_class", "routing_revision", "service_lane"}, nil, nil) && validFenceShape(object["fence"])
}

func validLaneShape(value any) bool {
	object, ok := asObject(value)
	if !ok || !exactKeys(object, "capacity", "declared", "lane", "queue_fence", "service_state", "telemetry", "zero_proof") || !typedFields(object, []string{"lane", "service_state"}, nil, nil) || !validFenceShape(object["queue_fence"]) {
		return false
	}
	capacity, ok := asObject(object["capacity"])
	if !ok || !exactKeys(capacity, "admitted", "current", "desired", "drain_started_at", "draining", "hard_max", "inflight", "last_scale_at", "running", "scale_down_step", "scale_up_step", "warm_floor") || !typedFields(capacity, nil, []string{"admitted", "current", "desired", "drain_started_at", "hard_max", "inflight", "last_scale_at", "running", "scale_down_step", "scale_up_step", "warm_floor"}, []string{"draining"}) {
		return false
	}
	declared, ok := asObject(object["declared"])
	if !ok || !exactKeys(declared, "assignment_count", "eligible_ready_count", "inflight_count", "oldest_eligible_age", "ready_count") || !typedFields(declared, nil, []string{"assignment_count", "eligible_ready_count", "inflight_count", "oldest_eligible_age", "ready_count"}, nil) {
		return false
	}
	telemetry, ok := asObject(object["telemetry"])
	if !ok || !exactKeys(telemetry, "error_budget_burn", "headroom_p05_ratio", "observed_at", "queue_oldest_age", "resource_saturated", "utilization_p95_ratio") || !typedFields(telemetry, nil, []string{"error_budget_burn", "headroom_p05_ratio", "observed_at", "queue_oldest_age", "utilization_p95_ratio"}, []string{"resource_saturated"}) {
		return false
	}
	return object["zero_proof"] == nil || validProofShape(object["zero_proof"])
}

func validProofShape(value any) bool {
	object, ok := asObject(value)
	if !ok || !exactKeys(object, "assignment_count", "capability_census_revision", "complete", "completed_at", "config_revision", "eligible_ready_count", "inflight_count", "oldest_eligible_since", "policy_revision", "queue_fence", "queue_revision", "ready_count", "routing_revision", "started_at") || !typedFields(object, []string{"capability_census_revision", "config_revision", "policy_revision", "queue_revision", "routing_revision"}, []string{"assignment_count", "completed_at", "eligible_ready_count", "inflight_count", "ready_count", "started_at"}, []string{"complete"}) || !validFenceShape(object["queue_fence"]) {
		return false
	}
	_, isNumber := object["oldest_eligible_since"].(json.Number)
	return object["oldest_eligible_since"] == nil || isNumber
}

func validEventShape(value any) bool {
	object, ok := asObject(value)
	return ok && exactKeys(object, "capability_census_revision", "config_revision", "event_at", "event_ordinal", "kind", "lane", "policy_revision", "queue_revision", "routing_revision", "work_ordinal") && typedFields(object, []string{"capability_census_revision", "config_revision", "kind", "lane", "policy_revision", "queue_revision", "routing_revision"}, []string{"event_at", "event_ordinal", "work_ordinal"}, nil)
}

func validFenceShape(value any) bool {
	object, ok := asObject(value)
	return ok && exactKeys(object, "claim_fence", "config_revision", "engine_owner", "queue_revision", "routing_epoch", "shard_id") && typedFields(object, []string{"config_revision", "engine_owner", "queue_revision", "shard_id"}, []string{"claim_fence", "routing_epoch"}, nil)
}

func typedFields(object map[string]any, stringsKeys, numberKeys, boolKeys []string) bool {
	for _, key := range stringsKeys {
		if _, ok := object[key].(string); !ok {
			return false
		}
	}
	for _, key := range numberKeys {
		if _, ok := object[key].(json.Number); !ok {
			return false
		}
	}
	for _, key := range boolKeys {
		if _, ok := object[key].(bool); !ok {
			return false
		}
	}
	return true
}

func strictParse(data []byte, finalLF bool) (any, error) {
	if len(data) == 0 || len(data) > maxDocument || !utf8.Valid(data) {
		return nil, errInvalidInput
	}
	payload := data
	if finalLF {
		if data[len(data)-1] != '\n' {
			return nil, errInvalidInput
		}
		payload = data[:len(data)-1]
	} else if data[len(data)-1] == '\n' {
		return nil, errInvalidInput
	}
	if len(payload) == 0 || bytes.IndexAny(payload, " \t\r\n") >= 0 || scanJSON(payload) != nil {
		return nil, errInvalidInput
	}
	value, err := decodeAny(payload)
	if err != nil {
		return nil, errInvalidInput
	}
	canonical, err := canonicalValue(value)
	if err != nil || !bytes.Equal(canonical, payload) {
		return nil, errInvalidInput
	}
	return value, nil
}

func decodeAny(data []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return nil, errInvalidInput
		}
		return nil, err
	}
	return value, nil
}

func canonicalValue(value any) ([]byte, error) {
	var output bytes.Buffer
	if err := writeCanonical(&output, value); err != nil {
		return nil, err
	}
	return output.Bytes(), nil
}

func writeCanonical(output *bytes.Buffer, value any) error {
	switch typed := value.(type) {
	case nil:
		output.WriteString("null")
	case bool:
		if typed {
			output.WriteString("true")
		} else {
			output.WriteString("false")
		}
	case string:
		data, err := json.Marshal(typed)
		if err != nil {
			return err
		}
		output.Write(data)
	case json.Number:
		output.WriteString(string(typed))
	case []any:
		output.WriteByte('[')
		for index, child := range typed {
			if index > 0 {
				output.WriteByte(',')
			}
			if err := writeCanonical(output, child); err != nil {
				return err
			}
		}
		output.WriteByte(']')
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		output.WriteByte('{')
		for index, key := range keys {
			if index > 0 {
				output.WriteByte(',')
			}
			if err := writeCanonical(output, key); err != nil {
				return err
			}
			output.WriteByte(':')
			if err := writeCanonical(output, typed[key]); err != nil {
				return err
			}
		}
		output.WriteByte('}')
	default:
		return errInvalidInput
	}
	return nil
}

func decodeExact(data []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return errInvalidInput
		}
		return err
	}
	return nil
}

func scanJSON(data []byte) error {
	scanner := jsonScanner{data: data}
	if err := scanner.value(1); err != nil || scanner.pos != len(data) {
		return errInvalidInput
	}
	return nil
}

type jsonScanner struct {
	data []byte
	pos  int
}

func (scanner *jsonScanner) value(depth int) error {
	if depth > maxDepth || scanner.pos >= len(scanner.data) {
		return errInvalidInput
	}
	switch scanner.data[scanner.pos] {
	case '{':
		return scanner.object(depth)
	case '[':
		return scanner.array(depth)
	case '"':
		_, err := scanner.string()
		return err
	case 't':
		return scanner.literal("true")
	case 'f':
		return scanner.literal("false")
	case 'n':
		return scanner.literal("null")
	default:
		return scanner.number()
	}
}

func (scanner *jsonScanner) literal(value string) error {
	if !bytes.HasPrefix(scanner.data[scanner.pos:], []byte(value)) {
		return errInvalidInput
	}
	scanner.pos += len(value)
	return nil
}

func (scanner *jsonScanner) object(depth int) error {
	scanner.pos++
	keys := map[string]bool{}
	if scanner.pos < len(scanner.data) && scanner.data[scanner.pos] == '}' {
		scanner.pos++
		return nil
	}
	for count := 0; ; count++ {
		if count >= maxObject || scanner.pos >= len(scanner.data) || scanner.data[scanner.pos] != '"' {
			return errInvalidInput
		}
		key, err := scanner.string()
		if err != nil || keys[key] {
			return errInvalidInput
		}
		keys[key] = true
		if scanner.pos >= len(scanner.data) || scanner.data[scanner.pos] != ':' {
			return errInvalidInput
		}
		scanner.pos++
		if err := scanner.value(depth + 1); err != nil {
			return err
		}
		if scanner.pos >= len(scanner.data) {
			return errInvalidInput
		}
		if scanner.data[scanner.pos] == '}' {
			scanner.pos++
			return nil
		}
		if scanner.data[scanner.pos] != ',' {
			return errInvalidInput
		}
		scanner.pos++
	}
}

func (scanner *jsonScanner) array(depth int) error {
	scanner.pos++
	if scanner.pos < len(scanner.data) && scanner.data[scanner.pos] == ']' {
		scanner.pos++
		return nil
	}
	for count := 0; ; count++ {
		if count >= maxArray || scanner.value(depth+1) != nil {
			return errInvalidInput
		}
		if scanner.pos >= len(scanner.data) {
			return errInvalidInput
		}
		if scanner.data[scanner.pos] == ']' {
			scanner.pos++
			return nil
		}
		if scanner.data[scanner.pos] != ',' {
			return errInvalidInput
		}
		scanner.pos++
	}
}

func (scanner *jsonScanner) string() (string, error) {
	start := scanner.pos
	scanner.pos++
	for scanner.pos < len(scanner.data) {
		character := scanner.data[scanner.pos]
		if character == '"' {
			scanner.pos++
			var value string
			if json.Unmarshal(scanner.data[start:scanner.pos], &value) != nil || len([]byte(value)) > maxString || (value != Format && disallowed(value)) {
				return "", errInvalidInput
			}
			return value, nil
		}
		if character < 0x20 {
			return "", errInvalidInput
		}
		if character == '\\' {
			scanner.pos++
			if scanner.pos >= len(scanner.data) {
				return "", errInvalidInput
			}
			if scanner.data[scanner.pos] == 'u' {
				if scanner.pos+4 >= len(scanner.data) {
					return "", errInvalidInput
				}
				scanner.pos += 4
			}
		}
		scanner.pos++
	}
	return "", errInvalidInput
}

func (scanner *jsonScanner) number() error {
	start := scanner.pos
	for scanner.pos < len(scanner.data) && !strings.ContainsRune(",]}", rune(scanner.data[scanner.pos])) {
		scanner.pos++
	}
	token := string(scanner.data[start:scanner.pos])
	if token == "" || strings.ContainsAny(token, "eE+-") || len(token) > 1 && token[0] == '0' && token[1] != '.' {
		return errInvalidInput
	}
	if strings.Contains(token, ".") {
		parts := strings.Split(token, ".")
		if len(parts) != 2 || parts[0] == "" || parts[1] == "" || len(parts[1]) > 6 || strings.HasSuffix(parts[1], "0") && parts[1] != "0" {
			return errInvalidInput
		}
		value, err := strconv.ParseFloat(token, 64)
		if err != nil || math.IsNaN(value) || math.IsInf(value, 0) || value < 0 || value > maxInteger {
			return errInvalidInput
		}
		return nil
	}
	value, err := strconv.ParseUint(token, 10, 64)
	if err != nil || value > maxInteger {
		return errInvalidInput
	}
	return nil
}

func asObject(value any) (map[string]any, bool) {
	object, ok := value.(map[string]any)
	return object, ok
}

func exactKeys(object map[string]any, keys ...string) bool {
	if len(object) != len(keys) {
		return false
	}
	for _, key := range keys {
		if _, ok := object[key]; !ok {
			return false
		}
	}
	return true
}

func sameOptional(left, right *uint64) bool {
	return left == nil && right == nil || left != nil && right != nil && *left == *right
}

func fenceID(fence Fence) fenceIdentity {
	return fenceIdentity{fence.QueueRevision, fence.ShardID, fence.RoutingEpoch, fence.EngineOwner, fence.ConfigRevision, fence.ClaimFence}
}

func addAll(values map[string]map[string]bool, reason string) {
	for _, lane := range laneNames {
		values[lane][reason] = true
	}
}

func activeWindow(now, start, duration uint64) bool {
	return now < start || now-start < duration
}

func priorityCredit(priority string) uint64 {
	return map[string]uint64{"first_time": 300, "monitor": 60, "detail": 0}[priority]
}

func priorityRank(priority string) int {
	return map[string]int{"first_time": 0, "monitor": 1, "detail": 2}[priority]
}

func validRatio(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && value >= 0 && value <= 1
}

func validID(value string) bool   { return safeID.MatchString(value) && !disallowed(value) }
func validLane(value string) bool { return value == "lightpanda" || value == "chromium" }
func validPriority(value string) bool {
	return value == "first_time" || value == "monitor" || value == "detail"
}
func validVerdict(value string) bool {
	return value == "permit" || value == "defer" || value == "deny" || value == "violation"
}
func validServiceState(value string) bool {
	return value == "admitted" || value == "unready" || value == "error" || value == "unsupported" || value == "full"
}

func disallowed(value string) bool {
	lower := strings.ToLower(value)
	if strings.ContainsAny(value, "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f\x7f") || strings.Contains(value, "://") || strings.Contains(lower, "www.") || strings.ContainsAny(value, "@?#/\\") || strings.Contains(lower, "authorization") || strings.Contains(lower, "bearer") || strings.Contains(lower, "token") || strings.Contains(lower, "secret") || strings.Contains(lower, "password") || strings.Contains(lower, "apikey") || strings.Contains(lower, "api_key") || strings.Contains(lower, "cookie") || strings.Contains(lower, "session") || strings.Contains(lower, "key=") {
		return true
	}
	return privatePattern.MatchString(value)
}

func isDigest(value string) bool {
	if len(value) != 64 || value != strings.ToLower(value) {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}

func min(left, right uint64) uint64 {
	if left < right {
		return left
	}
	return right
}

func max(left, right uint64) uint64 {
	if left > right {
		return left
	}
	return right
}
