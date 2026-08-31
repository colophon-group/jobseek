// Package browserlanesv1 implements the offline v1 browser-lane conformance
// model. It deliberately has no production scheduler, network, or service
// integration.
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
	Format       = "jobseek.browser-lanes.v1.conformance/v1"
	maxDocument  = 1_048_576
	maxDepth     = 12
	maxArray     = 4096
	maxObject    = 64
	maxString    = 4096
	maxCases     = 512
	maxItems     = 4096
	maxInteger   = 9_007_199_254_740_991
	telemetryAge = 30
	proofAge     = 30
	proofWindow  = 900
	drainWindow  = 30
	cooldown     = 60
)

var errInvalidInput = errors.New("invalid_input")

var safeID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$`)

// Corpus is the checked-in, deterministic conformance corpus.
type Corpus struct {
	Format string `json:"format"`
	Cases  []Case `json:"cases"`
}

type Case struct {
	ID           string `json:"id"`
	Input        Input  `json:"input"`
	Expected     Result `json:"expected"`
	ResultDigest string `json:"result_digest"`
}

// Result is intentionally the complete advisory output. It includes no
// queue/task/routing identifiers; an ordinal is the only work reference.
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

type Input struct {
	Now                      uint64          `json:"now"`
	PolicyRevision           string          `json:"policy_revision"`
	RoutingRevision          string          `json:"routing_revision"`
	QueueRevision            string          `json:"queue_revision"`
	ConfigRevision           string          `json:"config_revision"`
	CapabilityCensusRevision string          `json:"capability_census_revision"`
	Items                    []Item          `json:"items"`
	Lanes                    map[string]Lane `json:"lanes"`
}

type Item struct {
	Ordinal       uint64     `json:"ordinal"`
	WorkClass     string     `json:"work_class"`
	Priority      string     `json:"priority"`
	Lane          string     `json:"lane"`
	DueAt         uint64     `json:"due_at"`
	EligibleSince uint64     `json:"eligible_since"`
	Assignment    Assignment `json:"assignment"`
	Queue         Queue      `json:"queue"`
	Admission     Admission  `json:"admission"`
}

type Assignment struct {
	Backend            string         `json:"backend"`
	AssignmentRevision string         `json:"assignment_revision"`
	ImmutableCopy      AssignmentCopy `json:"immutable_copy"`
}

type AssignmentCopy struct {
	Backend            string `json:"backend"`
	AssignmentRevision string `json:"assignment_revision"`
}

type Queue struct {
	RouteRevision  string `json:"route_revision"`
	ConfigRevision string `json:"config_revision"`
	Epoch          uint64 `json:"epoch"`
	Owner          string `json:"owner"`
	ClaimFence     uint64 `json:"claim_fence"`
}

type Admission struct {
	Verdict        string `json:"verdict"`
	PolicyRevision string `json:"policy_revision"`
}

type Lane struct {
	Lane                     string     `json:"lane"`
	RoutingRevision          string     `json:"routing_revision"`
	PolicyRevision           string     `json:"policy_revision"`
	QueueRevision            string     `json:"queue_revision"`
	ConfigRevision           string     `json:"config_revision"`
	CapabilityCensusRevision string     `json:"capability_census_revision"`
	QueueShardID             string     `json:"queue_shard_id"`
	RoutingEpoch             uint64     `json:"routing_epoch"`
	EngineOwner              string     `json:"engine_owner"`
	Capacity                 Capacity   `json:"capacity"`
	Service                  Service    `json:"service"`
	Telemetry                Telemetry  `json:"telemetry"`
	Declared                 Declared   `json:"declared"`
	ZeroProof                *ZeroProof `json:"zero_proof"`
}

type Capacity struct {
	Current        uint64 `json:"current"`
	Desired        uint64 `json:"desired"`
	Inflight       uint64 `json:"inflight"`
	Admitted       uint64 `json:"admitted"`
	Running        uint64 `json:"running"`
	WarmFloor      uint64 `json:"warm_floor"`
	HardMax        uint64 `json:"hard_max"`
	ScaleUpStep    uint64 `json:"scale_up_step"`
	ScaleDownStep  uint64 `json:"scale_down_step"`
	LastScaleAt    uint64 `json:"last_scale_at"`
	Draining       bool   `json:"draining"`
	DrainStartedAt uint64 `json:"drain_started_at"`
}

type Service struct {
	Ready     bool   `json:"ready"`
	Admission string `json:"admission"`
}

type Telemetry struct {
	ObservedAt          uint64  `json:"observed_at"`
	QueueOldestAge      uint64  `json:"queue_oldest_age"`
	UtilizationP95Ratio float64 `json:"utilization_p95_ratio"`
	HeadroomP05Ratio    float64 `json:"headroom_p05_ratio"`
	ErrorBudgetBurn     float64 `json:"error_budget_burn"`
	ResourceSaturated   bool    `json:"resource_saturated"`
}

type Declared struct {
	EligibleReady     uint64 `json:"eligible_ready"`
	OldestEligibleAge uint64 `json:"oldest_eligible_age"`
}

// ZeroProof accepts the resolved v1 shape. QueueRevision remains the lane
// snapshot binding introduced by the closed input shape.
type ZeroProof struct {
	StartedAt                uint64  `json:"started_at"`
	CompletedAt              uint64  `json:"completed_at"`
	RoutingRevision          string  `json:"routing_revision"`
	PolicyRevision           string  `json:"policy_revision"`
	QueueShardID             string  `json:"queue_shard_id"`
	RoutingEpoch             uint64  `json:"routing_epoch"`
	EngineOwner              string  `json:"engine_owner"`
	ConfigRevision           string  `json:"config_revision"`
	CapabilityCensusRevision string  `json:"capability_census_revision"`
	QueueCount               uint64  `json:"queue_count"`
	InflightCount            uint64  `json:"inflight_count"`
	AssignmentCount          uint64  `json:"assignment_count"`
	EligibleReadyCount       uint64  `json:"eligible_ready_count"`
	OldestEligibleSince      *uint64 `json:"oldest_eligible_since"`
}

// DecodeCorpus accepts only a bounded, privacy-safe JSON corpus. All parser
// failures intentionally collapse to invalid_input so untrusted input cannot
// be reflected in a diagnostic or digest.
func DecodeCorpus(data []byte) (Corpus, error) {
	// Corpus envelope validation below is deliberately separate from the raw
	// payload decoder: the generator owns the final-LF corpus serialization.
	var raw map[string]json.RawMessage
	if err := decodeExact(data, &raw); err != nil || !exactKeys(raw, "format", "cases") {
		return Corpus{}, errInvalidInput
	}
	if !validCorpusShape(raw) {
		return Corpus{}, errInvalidInput
	}
	var corpus Corpus
	if err := decodeExact(data, &corpus); err != nil || corpus.Format != Format || len(corpus.Cases) > maxCases {
		return Corpus{}, errInvalidInput
	}
	for index, testCase := range corpus.Cases {
		if !safeID.MatchString(testCase.ID) || !isDigest(testCase.ResultDigest) || validateInput(testCase.Input) != nil || validateResult(testCase.Expected) != nil {
			return Corpus{}, errInvalidInput
		}
		if testCase.ID == "" || index > maxCases { // retain a total, bounded failure path.
			return Corpus{}, errInvalidInput
		}
	}
	return corpus, nil
}

// DecodeInput is exposed for malformed-vector conformance tests.
func DecodeInput(data []byte) (Input, error) {
	if err := scanJSON(data); err != nil {
		return Input{}, errInvalidInput
	}
	var raw map[string]json.RawMessage
	if err := decodeExact(data, &raw); err != nil || !validInputShape(raw) {
		return Input{}, errInvalidInput
	}
	var input Input
	if err := decodeExact(data, &input); err != nil || validateInput(input) != nil {
		return Input{}, errInvalidInput
	}
	return input, nil
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
			return errors.New("trailing_json")
		}
		return err
	}
	return nil
}

// Evaluate evaluates lanes independently; all shared validation has already
// been attributed to the affected item lane(s) before any dispatch decision.
func Evaluate(input Input) Result {
	if validateInput(input) != nil {
		return Result{}
	}
	decisions := make(map[string]Decision, 2)
	for _, name := range []string{"lightpanda", "chromium"} {
		decisions[name] = evaluateLane(input, name)
	}
	return Result{Lanes: decisions}
}

func RunCase(testCase Case) (Result, error) {
	if !safeID.MatchString(testCase.ID) || validateInput(testCase.Input) != nil || validateResult(testCase.Expected) != nil {
		return Result{}, errInvalidInput
	}
	return Evaluate(testCase.Input), nil
}

func evaluateLane(input Input, name string) Decision {
	lane := input.Lanes[name]
	base, ok := capacityBase(lane.Capacity)
	if !ok {
		return freeze(name, max(lane.Capacity.Inflight, lane.Capacity.WarmFloor), "invalid_input")
	}
	if failures := itemFailures(input, name); len(failures) > 0 {
		return freeze(name, base, failures...)
	}
	eligible := eligibleItems(input, name)
	oldest := uint64(0)
	for _, item := range eligible {
		oldest = max(oldest, input.Now-item.EligibleSince)
	}
	if lane.Declared.EligibleReady != uint64(len(eligible)) || lane.Declared.OldestEligibleAge != oldest {
		return freeze(name, base, "conservation_failure")
	}
	if reason := laneFreeze(input, name); reason != "" {
		return freeze(name, base, reason)
	}
	if lane.Telemetry.UtilizationP95Ratio > .85 || lane.Telemetry.HeadroomP05Ratio < .15 {
		return deferDecision(name, base, "capacity_headroom_unsafe")
	}
	if len(eligible) > 0 {
		if lane.Capacity.Admitted > lane.Capacity.Inflight {
			selected := selectItem(input.Now, eligible)
			return Decision{Decision: "claim", DesiredConcurrency: base, Lane: name, Reasons: []string{}, SelectedItemIndex: pointer(selected.Ordinal)}
		}
		if lane.Capacity.Draining && lane.Capacity.DrainStartedAt > 0 && !elapsed(input.Now, lane.Capacity.DrainStartedAt, drainWindow) {
			return deferDecision(name, base, "drain_active")
		}
		if !cooldownComplete(input.Now, lane.Capacity.LastScaleAt) {
			return deferDecision(name, base, "scale_cooldown_active")
		}
		if base >= lane.Capacity.HardMax {
			return deferDecision(name, base, "hard_max_reached")
		}
		return deferDecision(name, min(lane.Capacity.HardMax, base+lane.Capacity.ScaleUpStep), "scale_up_requested")
	}
	if zero, reason := zeroTarget(input, name); reason == "" {
		return deferDecision(name, zero, "no_eligible_backlog")
	} else {
		return deferDecision(name, base, "no_eligible_backlog", reason)
	}
}

func laneFreeze(input Input, name string) string {
	lane := input.Lanes[name]
	if !lane.Service.Ready {
		switch lane.Service.Admission {
		case "unready":
			return "service_unready"
		case "error":
			return "service_error"
		case "unsupported":
			return "service_unsupported"
		case "full":
			return "service_full"
		default:
			return "invalid_input"
		}
	}
	if lane.Service.Admission != "admitted" {
		return "invalid_input"
	}
	if input.Now < lane.Telemetry.ObservedAt || input.Now-lane.Telemetry.ObservedAt > telemetryAge {
		return "telemetry_stale"
	}
	if lane.Telemetry.ErrorBudgetBurn > 1 {
		return "error_budget_exhausted"
	}
	if lane.Telemetry.ResourceSaturated {
		return "resource_saturation"
	}
	return ""
}

func itemFailures(input Input, name string) []string {
	failures := []string{}
	fences := map[[2]uint64]bool{}
	lane := input.Lanes[name]
	for _, item := range input.Items {
		if item.Lane != name {
			continue
		}
		if item.Assignment.Backend != item.Assignment.ImmutableCopy.Backend || item.Assignment.AssignmentRevision != item.Assignment.ImmutableCopy.AssignmentRevision {
			failures = append(failures, "assignment_mutated")
			continue
		}
		if item.Assignment.Backend != item.Lane {
			failures = append(failures, "assignment_lane_mismatch")
			continue
		}
		if item.Assignment.AssignmentRevision != input.RoutingRevision {
			failures = append(failures, "revision_mismatch")
			continue
		}
		if item.Queue.RouteRevision != input.RoutingRevision || item.Queue.ConfigRevision != input.ConfigRevision || item.Queue.Epoch != lane.RoutingEpoch || item.Queue.Owner != lane.EngineOwner {
			failures = append(failures, "queue_fence_invalid")
			continue
		}
		if item.Admission.PolicyRevision != input.PolicyRevision || item.Admission.Verdict == "violation" {
			failures = append(failures, "policy_violation")
			continue
		}
		key := [2]uint64{item.Queue.Epoch, item.Queue.ClaimFence}
		if fences[key] {
			failures = append(failures, "queue_fence_invalid")
		}
		fences[key] = true
	}
	sort.Strings(failures)
	return dedupe(failures)
}

func eligibleItems(input Input, name string) []Item {
	items := make([]Item, 0)
	for _, item := range input.Items {
		if item.Lane == name && item.DueAt <= input.Now && item.Admission.Verdict == "permit" {
			items = append(items, item)
		}
	}
	return items
}

func selectItem(now uint64, items []Item) Item {
	sort.Slice(items, func(i, j int) bool {
		a, b := items[i], items[j]
		ageA, ageB := now-a.EligibleSince, now-b.EligibleSince
		if ageA >= proofWindow || ageB >= proofWindow {
			if ageA != ageB {
				return ageA > ageB
			}
		} else {
			scoreA, scoreB := ageA+priorityCredit(a.Priority), ageB+priorityCredit(b.Priority)
			if scoreA != scoreB {
				return scoreA > scoreB
			}
		}
		if a.EligibleSince != b.EligibleSince {
			return a.EligibleSince < b.EligibleSince
		}
		if priorityRank(a.Priority) != priorityRank(b.Priority) {
			return priorityRank(a.Priority) > priorityRank(b.Priority)
		}
		return a.Ordinal < b.Ordinal
	})
	return items[0]
}

func zeroTarget(input Input, name string) (uint64, string) {
	lane := input.Lanes[name]
	proof := lane.ZeroProof
	if proof == nil {
		return 0, "zero_proof_absent"
	}
	if input.Now < proof.CompletedAt || input.Now-proof.CompletedAt > proofAge {
		return 0, "zero_proof_stale"
	}
	if proof.CompletedAt < proof.StartedAt || proof.CompletedAt-proof.StartedAt < proofWindow {
		return 0, "zero_proof_invalid"
	}
	if !proofMatches(lane, proof) {
		return 0, "zero_proof_revision_mismatch"
	}
	eligible := eligibleItems(input, name)
	count := uint64(0)
	for _, item := range input.Items {
		if item.Lane == name {
			count++
		}
	}
	var oldest *uint64
	for _, item := range eligible {
		if oldest == nil || item.EligibleSince < *oldest {
			value := item.EligibleSince
			oldest = &value
		}
	}
	if proof.QueueCount != 0 || proof.InflightCount != lane.Capacity.Inflight || proof.AssignmentCount != count || proof.EligibleReadyCount != uint64(len(eligible)) || (proof.OldestEligibleSince == nil) != (oldest == nil) || (oldest != nil && *proof.OldestEligibleSince != *oldest) {
		return 0, "zero_proof_invalid"
	}
	if proof.QueueCount != 0 || proof.InflightCount != 0 || proof.AssignmentCount != 0 || proof.EligibleReadyCount != 0 {
		return 0, "zero_proof_demand_present"
	}
	for _, item := range input.Items {
		if item.Lane == name && item.EligibleSince >= proof.CompletedAt {
			return 0, "zero_proof_invalid"
		}
	}
	if lane.Capacity.Draining && !elapsed(input.Now, lane.Capacity.DrainStartedAt, drainWindow) {
		return 0, "drain_active"
	}
	if !cooldownComplete(input.Now, lane.Capacity.LastScaleAt) {
		return 0, "scale_cooldown_active"
	}
	if lane.Capacity.Inflight != 0 || lane.Capacity.Running != 0 || lane.Capacity.Admitted != 0 || lane.Capacity.Current != 0 {
		return 0, "zero_proof_demand_present"
	}
	return 0, ""
}

func proofMatches(lane Lane, proof *ZeroProof) bool {
	return proof.RoutingRevision == lane.RoutingRevision && proof.PolicyRevision == lane.PolicyRevision && proof.QueueShardID == lane.QueueShardID && proof.RoutingEpoch == lane.RoutingEpoch && proof.EngineOwner == lane.EngineOwner && proof.ConfigRevision == lane.ConfigRevision && proof.CapabilityCensusRevision == lane.CapabilityCensusRevision
}

func cooldownComplete(now, last uint64) bool   { return last == 0 || elapsed(now, last, cooldown) }
func elapsed(now, start, duration uint64) bool { return now >= start && now-start >= duration }

func freeze(lane string, desired uint64, reasons ...string) Decision {
	sort.Strings(reasons)
	return Decision{Decision: "freeze", DesiredConcurrency: desired, Lane: lane, Reasons: dedupe(reasons)}
}
func deferDecision(lane string, desired uint64, reasons ...string) Decision {
	sort.Strings(reasons)
	return Decision{Decision: "defer", DesiredConcurrency: desired, Lane: lane, Reasons: dedupe(reasons)}
}
func pointer(value uint64) *uint64 { return &value }
func min(a, b uint64) uint64 {
	if a < b {
		return a
	}
	return b
}
func max(a, b uint64) uint64 {
	if a > b {
		return a
	}
	return b
}
func clamp(value, low, high uint64) uint64 {
	if value < low {
		return low
	}
	if value > high {
		return high
	}
	return value
}
func capacityBase(c Capacity) (uint64, bool) {
	if !(c.Inflight <= c.Running && c.Running <= c.Admitted && c.Admitted <= c.Current && c.Current <= c.HardMax && c.HardMax <= 4096 && c.WarmFloor <= c.HardMax && c.Desired <= 4096) {
		return 0, false
	}
	return clamp(c.Current, max(c.Inflight, c.WarmFloor), c.HardMax), true
}
func priorityCredit(priority string) uint64 {
	if priority == "first_time" {
		return 300
	}
	if priority == "monitor" {
		return 60
	}
	return 0
}
func priorityRank(priority string) int {
	if priority == "first_time" {
		return 3
	}
	if priority == "monitor" {
		return 2
	}
	return 1
}
func dedupe(values []string) []string {
	result := values[:0]
	for _, value := range values {
		if len(result) == 0 || result[len(result)-1] != value {
			result = append(result, value)
		}
	}
	return result
}

func validateInput(input Input) error {
	if input.Now > maxInteger || len(input.Items) > maxItems || len(input.Lanes) != 2 || input.Lanes["lightpanda"].Lane != "lightpanda" || input.Lanes["chromium"].Lane != "chromium" || !validID(input.PolicyRevision) || !validID(input.RoutingRevision) || !validID(input.QueueRevision) || !validID(input.ConfigRevision) || !validID(input.CapabilityCensusRevision) {
		return errInvalidInput
	}
	for index, item := range input.Items {
		if item.Ordinal != uint64(index) || validateItem(input, item) != nil {
			return errInvalidInput
		}
	}
	for _, name := range []string{"lightpanda", "chromium"} {
		lane := input.Lanes[name]
		if validateLane(input, lane, name) != nil {
			return errInvalidInput
		}
	}
	return nil
}

func validateItem(input Input, item Item) error {
	if item.Ordinal > maxInteger || !validWorkClass(item.WorkClass) || !validPriority(item.Priority) || !validLane(item.Lane) || item.DueAt > input.Now || item.EligibleSince > input.Now || !validLane(item.Assignment.Backend) || !validID(item.Assignment.AssignmentRevision) || !validLane(item.Assignment.ImmutableCopy.Backend) || !validID(item.Assignment.ImmutableCopy.AssignmentRevision) || !validID(item.Queue.RouteRevision) || !validID(item.Queue.ConfigRevision) || item.Queue.Epoch > maxInteger || item.Queue.ClaimFence > maxInteger || !validID(item.Queue.Owner) || !validVerdict(item.Admission.Verdict) || !validID(item.Admission.PolicyRevision) {
		return errInvalidInput
	}
	return nil
}

func validateLane(input Input, lane Lane, name string) error {
	c := lane.Capacity
	if lane.Lane != name || lane.RoutingRevision != input.RoutingRevision || lane.PolicyRevision != input.PolicyRevision || lane.QueueRevision != input.QueueRevision || lane.ConfigRevision != input.ConfigRevision || lane.CapabilityCensusRevision != input.CapabilityCensusRevision || !validID(lane.QueueShardID) || !validID(lane.EngineOwner) || lane.RoutingEpoch > maxInteger || c.HardMax > 4096 || c.Current > 4096 || c.Desired > 4096 || c.Inflight > 4096 || c.Admitted > 4096 || c.Running > 4096 || c.WarmFloor > 4096 || c.ScaleUpStep == 0 || c.ScaleUpStep > 4096 || c.ScaleDownStep == 0 || c.ScaleDownStep > 4096 || c.LastScaleAt > input.Now || c.DrainStartedAt > input.Now || !validService(lane.Service) || !validTelemetry(input.Now, lane.Telemetry) || lane.ZeroProof != nil && !validProof(input.Now, lane.ZeroProof) {
		return errInvalidInput
	}
	return nil
}

func validProof(now uint64, proof *ZeroProof) bool {
	return proof.StartedAt <= now && proof.CompletedAt <= now && proof.CompletedAt >= proof.StartedAt && validID(proof.RoutingRevision) && validID(proof.PolicyRevision) && validID(proof.QueueShardID) && validID(proof.EngineOwner) && validID(proof.ConfigRevision) && validID(proof.CapabilityCensusRevision)
}
func validTelemetry(now uint64, telemetry Telemetry) bool {
	return telemetry.ObservedAt <= now && telemetry.QueueOldestAge <= maxInteger && validRatio(telemetry.UtilizationP95Ratio) && validRatio(telemetry.HeadroomP05Ratio) && validBurn(telemetry.ErrorBudgetBurn)
}
func validRatio(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && value >= 0 && value <= 1
}
func validBurn(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && value >= 0 && value <= maxInteger
}
func validID(value string) bool        { return safeID.MatchString(value) && !disallowed(value) }
func validLane(value string) bool      { return value == "lightpanda" || value == "chromium" }
func validWorkClass(value string) bool { return value == "monitor" || value == "detail" }
func validPriority(value string) bool {
	return value == "first_time" || value == "monitor" || value == "detail"
}
func validVerdict(value string) bool {
	return value == "permit" || value == "defer" || value == "deny" || value == "violation"
}
func validService(value Service) bool {
	return (value.Ready && value.Admission == "admitted") || (!value.Ready && (value.Admission == "unready" || value.Admission == "error" || value.Admission == "unsupported" || value.Admission == "full"))
}

var reasons = map[string]bool{"capacity_headroom_unsafe": true, "no_eligible_backlog": true, "scale_up_requested": true, "scale_cooldown_active": true, "hard_max_reached": true, "drain_active": true, "zero_proof_absent": true, "zero_proof_stale": true, "zero_proof_invalid": true, "zero_proof_revision_mismatch": true, "zero_proof_demand_present": true, "error_budget_exhausted": true, "resource_saturation": true, "telemetry_stale": true, "invalid_input": true, "conservation_failure": true, "assignment_invalid": true, "assignment_mutated": true, "assignment_lane_mismatch": true, "revision_mismatch": true, "queue_fence_invalid": true, "policy_violation": true, "service_unready": true, "service_error": true, "service_unsupported": true, "service_full": true, "fallback_attempted": true}

func validateResult(result Result) error {
	if len(result.Lanes) != 2 {
		return errInvalidInput
	}
	for _, name := range []string{"lightpanda", "chromium"} {
		decision, exists := result.Lanes[name]
		if !exists {
			return errInvalidInput
		}
		if decision.Lane != name || (decision.Decision != "claim" && decision.Decision != "defer" && decision.Decision != "freeze") || decision.DesiredConcurrency > 4096 || !sort.StringsAreSorted(decision.Reasons) || len(decision.Reasons) > len(reasons) {
			return errInvalidInput
		}
		for offset, reason := range decision.Reasons {
			if !reasons[reason] || (offset > 0 && decision.Reasons[offset-1] == reason) {
				return errInvalidInput
			}
		}
		if decision.Decision == "claim" {
			if decision.SelectedItemIndex == nil || len(decision.Reasons) != 0 {
				return errInvalidInput
			}
		} else if decision.SelectedItemIndex != nil {
			return errInvalidInput
		}
	}
	return nil
}

func CanonicalBytes(result Result) ([]byte, error) {
	if validateResult(result) != nil {
		return nil, errInvalidInput
	}
	return json.Marshal(result)
}
func Digest(result Result) (string, error) {
	data, err := CanonicalBytes(result)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]), nil
}

func exactKeys(object map[string]json.RawMessage, keys ...string) bool {
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
func isDigest(value string) bool {
	if len(value) != 64 {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil && value == strings.ToLower(value)
}

func rawObject(value json.RawMessage) (map[string]json.RawMessage, bool) {
	var object map[string]json.RawMessage
	if decodeExact(value, &object) != nil || object == nil {
		return nil, false
	}
	return object, true
}

func rawArray(value json.RawMessage) ([]json.RawMessage, bool) {
	var array []json.RawMessage
	if decodeExact(value, &array) != nil || array == nil {
		return nil, false
	}
	return array, true
}

func validCorpusShape(root map[string]json.RawMessage) bool {
	cases, ok := rawArray(root["cases"])
	if !ok || len(cases) > maxCases {
		return false
	}
	for _, rawCase := range cases {
		testCase, ok := rawObject(rawCase)
		if !ok || !exactKeys(testCase, "id", "input", "expected", "result_digest") || !validInputRaw(testCase["input"]) || !validResultRaw(testCase["expected"]) {
			return false
		}
	}
	return true
}

func validInputShape(root map[string]json.RawMessage) bool { return validInputRaw(mustRaw(root)) }
func mustRaw(value map[string]json.RawMessage) json.RawMessage {
	data, _ := json.Marshal(value)
	return data
}

func validInputRaw(raw json.RawMessage) bool {
	input, ok := rawObject(raw)
	if !ok || !exactKeys(input, "now", "policy_revision", "routing_revision", "queue_revision", "config_revision", "capability_census_revision", "items", "lanes") {
		return false
	}
	items, ok := rawArray(input["items"])
	if !ok || len(items) > maxItems {
		return false
	}
	for _, rawItem := range items {
		item, ok := rawObject(rawItem)
		if !ok || !exactKeys(item, "ordinal", "work_class", "priority", "lane", "due_at", "eligible_since", "assignment", "queue", "admission") || !validItemRaw(item) {
			return false
		}
	}
	lanes, ok := rawObject(input["lanes"])
	if !ok || !exactKeys(lanes, "lightpanda", "chromium") {
		return false
	}
	for _, name := range []string{"lightpanda", "chromium"} {
		if !validLaneRaw(lanes[name]) {
			return false
		}
	}
	return true
}

func validItemRaw(item map[string]json.RawMessage) bool {
	assignment, ok := rawObject(item["assignment"])
	if !ok || !exactKeys(assignment, "backend", "assignment_revision", "immutable_copy") {
		return false
	}
	copy, ok := rawObject(assignment["immutable_copy"])
	if !ok || !exactKeys(copy, "backend", "assignment_revision") {
		return false
	}
	queue, ok := rawObject(item["queue"])
	if !ok || !exactKeys(queue, "route_revision", "config_revision", "epoch", "owner", "claim_fence") {
		return false
	}
	admission, ok := rawObject(item["admission"])
	return ok && exactKeys(admission, "verdict", "policy_revision")
}

func validLaneRaw(raw json.RawMessage) bool {
	lane, ok := rawObject(raw)
	if !ok || !exactKeys(lane, "lane", "routing_revision", "policy_revision", "queue_revision", "config_revision", "capability_census_revision", "queue_shard_id", "routing_epoch", "engine_owner", "capacity", "service", "telemetry", "declared", "zero_proof") {
		return false
	}
	capacity, ok := rawObject(lane["capacity"])
	if !ok || !exactKeys(capacity, "current", "desired", "inflight", "admitted", "running", "warm_floor", "hard_max", "scale_up_step", "scale_down_step", "last_scale_at", "draining", "drain_started_at") {
		return false
	}
	service, ok := rawObject(lane["service"])
	if !ok || !exactKeys(service, "ready", "admission") {
		return false
	}
	telemetry, ok := rawObject(lane["telemetry"])
	if !ok || !exactKeys(telemetry, "observed_at", "queue_oldest_age", "utilization_p95_ratio", "headroom_p05_ratio", "error_budget_burn", "resource_saturated") {
		return false
	}
	declared, ok := rawObject(lane["declared"])
	if !ok || !exactKeys(declared, "eligible_ready", "oldest_eligible_age") {
		return false
	}
	if proof, exists := lane["zero_proof"]; exists && string(proof) != "null" {
		object, ok := rawObject(proof)
		return ok && exactKeys(object, "routing_revision", "policy_revision", "queue_shard_id", "routing_epoch", "engine_owner", "config_revision", "capability_census_revision", "started_at", "completed_at", "queue_count", "inflight_count", "assignment_count", "eligible_ready_count", "oldest_eligible_since")
	}
	return true
}

func validResultRaw(raw json.RawMessage) bool {
	result, ok := rawObject(raw)
	if !ok || !exactKeys(result, "lanes") {
		return false
	}
	decisions, ok := rawObject(result["lanes"])
	if !ok || !exactKeys(decisions, "lightpanda", "chromium") {
		return false
	}
	for _, rawDecision := range decisions {
		decision, ok := rawObject(rawDecision)
		if !ok || !exactKeys(decision, "decision", "desired_concurrency", "lane", "reasons", "selected_item_index") {
			return false
		}
	}
	return true
}

// scanJSON is a small bounded JSON lexer. It rejects duplicate keys, unsafe
// strings, noncanonical numbers, over-depth/container input, and trailing data
// before encoding/json gets a chance to expose implementation diagnostics.
func scanJSON(data []byte) error {
	if len(data) == 0 || len(data) > maxDocument || !utf8.Valid(data) {
		return errInvalidInput
	}
	canonical := data
	if canonical[len(canonical)-1] == '\n' {
		canonical = canonical[:len(canonical)-1]
	}
	if len(canonical) == 0 || bytes.IndexAny(canonical, " \t\r\n") >= 0 {
		return errInvalidInput
	}
	s := jsonScanner{data: canonical}
	s.skipSpace()
	if err := s.value(1); err != nil {
		return err
	}
	s.skipSpace()
	if s.pos != len(data) {
		return errInvalidInput
	}
	return nil
}

type jsonScanner struct {
	data []byte
	pos  int
}

func (s *jsonScanner) skipSpace() {
	for s.pos < len(s.data) && (s.data[s.pos] == ' ' || s.data[s.pos] == '\n' || s.data[s.pos] == '\r' || s.data[s.pos] == '\t') {
		s.pos++
	}
}
func (s *jsonScanner) value(depth int) error {
	if depth > maxDepth || s.pos >= len(s.data) {
		return errInvalidInput
	}
	switch s.data[s.pos] {
	case '{':
		return s.object(depth)
	case '[':
		return s.array(depth)
	case '"':
		_, err := s.string()
		return err
	case 't':
		return s.literal("true")
	case 'f':
		return s.literal("false")
	case 'n':
		return s.literal("null")
	default:
		return s.number()
	}
}
func (s *jsonScanner) literal(value string) error {
	if !bytes.HasPrefix(s.data[s.pos:], []byte(value)) {
		return errInvalidInput
	}
	s.pos += len(value)
	return nil
}
func (s *jsonScanner) object(depth int) error {
	s.pos++
	s.skipSpace()
	keys := map[string]bool{}
	if s.pos < len(s.data) && s.data[s.pos] == '}' {
		s.pos++
		return nil
	}
	for count := 0; ; count++ {
		if count >= maxObject || s.pos >= len(s.data) || s.data[s.pos] != '"' {
			return errInvalidInput
		}
		key, err := s.string()
		if err != nil || keys[key] {
			return errInvalidInput
		}
		keys[key] = true
		s.skipSpace()
		if s.pos >= len(s.data) || s.data[s.pos] != ':' {
			return errInvalidInput
		}
		s.pos++
		s.skipSpace()
		if err := s.value(depth + 1); err != nil {
			return err
		}
		s.skipSpace()
		if s.pos >= len(s.data) {
			return errInvalidInput
		}
		if s.data[s.pos] == '}' {
			s.pos++
			return nil
		}
		if s.data[s.pos] != ',' {
			return errInvalidInput
		}
		s.pos++
		s.skipSpace()
	}
}
func (s *jsonScanner) array(depth int) error {
	s.pos++
	s.skipSpace()
	if s.pos < len(s.data) && s.data[s.pos] == ']' {
		s.pos++
		return nil
	}
	for count := 0; ; count++ {
		if count >= maxArray {
			return errInvalidInput
		}
		if err := s.value(depth + 1); err != nil {
			return err
		}
		s.skipSpace()
		if s.pos >= len(s.data) {
			return errInvalidInput
		}
		if s.data[s.pos] == ']' {
			s.pos++
			return nil
		}
		if s.data[s.pos] != ',' {
			return errInvalidInput
		}
		s.pos++
		s.skipSpace()
	}
}
func (s *jsonScanner) string() (string, error) {
	start := s.pos
	s.pos++
	for s.pos < len(s.data) {
		c := s.data[s.pos]
		if c == '"' {
			s.pos++
			raw := s.data[start:s.pos]
			var value string
			if json.Unmarshal(raw, &value) != nil || len(value) > maxString || (value != Format && disallowed(value)) {
				return "", errInvalidInput
			}
			return value, nil
		}
		if c < 0x20 {
			return "", errInvalidInput
		}
		if c == '\\' {
			s.pos++
			if s.pos >= len(s.data) {
				return "", errInvalidInput
			}
			if s.data[s.pos] == 'u' {
				if s.pos+4 >= len(s.data) {
					return "", errInvalidInput
				}
				s.pos += 4
			}
		}
		s.pos++
	}
	return "", errInvalidInput
}
func (s *jsonScanner) number() error {
	start := s.pos
	for s.pos < len(s.data) && !strings.ContainsRune(" \n\r\t,]}", rune(s.data[s.pos])) {
		s.pos++
	}
	token := string(s.data[start:s.pos])
	if token == "" || strings.ContainsAny(token, "eE+") || strings.HasPrefix(token, "-") || (len(token) > 1 && token[0] == '0' && token[1] != '.') {
		return errInvalidInput
	}
	if strings.Contains(token, ".") {
		parts := strings.Split(token, ".")
		if len(parts) != 2 || parts[0] == "" || parts[1] == "" || len(parts[1]) > 6 {
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
func disallowed(value string) bool {
	lower := strings.ToLower(value)
	if strings.ContainsAny(value, "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f") || strings.Contains(value, "://") || strings.Contains(lower, "www.") || strings.ContainsAny(value, "@?#/") || strings.Contains(value, "\\\\") || strings.Contains(lower, "authorization") || strings.Contains(lower, "bearer") || strings.Contains(lower, "token") || strings.Contains(lower, "secret") || strings.Contains(lower, "password") || strings.Contains(lower, "apikey") || strings.Contains(lower, "api_key") || strings.Contains(lower, "cookie") || strings.Contains(lower, "session") || strings.Contains(lower, "key=") {
		return true
	}
	return regexp.MustCompile(`(?i)(?:\d{1,3}\.){3}\d{1,3}|\[[0-9a-f:]+\]|(?:[a-z0-9-]+\.)+[a-z]{2,}`).MatchString(value)
}
