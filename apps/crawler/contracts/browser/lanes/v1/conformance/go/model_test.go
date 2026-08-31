package browserlanesv1

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// The checked-in Python-generated corpus is intentionally not owned here.
// Once it lands, this test reads it without a Go-side fixture copy.
func TestCanonicalCorpusParityWhenPresent(t *testing.T) {
	path := os.Getenv("BROWSER_LANES_FIXTURE")
	if path == "" {
		path = "../../fixtures/scenarios.json"
	}
	data, err := osReadFile(path)
	if err != nil {
		if strings.Contains(err.Error(), "no such file") {
			t.Skip("canonical scenarios are owned by the corpus generator")
		}
		t.Fatal(err)
	}
	corpus, err := DecodeCorpus(data)
	if err != nil {
		t.Fatal(err)
	}
	for _, testCase := range corpus.Cases {
		t.Run(testCase.ID, func(t *testing.T) {
			result, err := RunCase(testCase)
			if err != nil {
				t.Fatal(err)
			}
			actual, err := CanonicalBytes(result)
			if err != nil {
				t.Fatal(err)
			}
			expected, err := CanonicalBytes(testCase.Expected)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(actual, expected) {
				t.Fatalf("canonical result differs\nactual: %s\nexpected: %s", actual, expected)
			}
			digest, err := Digest(result)
			if err != nil || digest != testCase.ResultDigest {
				t.Fatalf("result digest = %q, %v; want %q", digest, err, testCase.ResultDigest)
			}
		})
	}
}

func TestLaneSafetyAndWorkConservationAreIndependent(t *testing.T) {
	input := validInput()
	input.Items = []Item{validItem(0, "lightpanda", "detail")}
	input.Lanes["lightpanda"] = withDeclared(input, "lightpanda")
	result := Evaluate(input)
	if got := result.Lanes["lightpanda"]; got.Decision != "claim" || got.SelectedItemIndex == nil || *got.SelectedItemIndex != 0 || got.DesiredConcurrency != 1 {
		t.Fatalf("lightpanda decision = %+v", got)
	}
	if got := result.Lanes["chromium"]; got.Decision != "defer" || !sameReasons(got.Reasons, []string{"no_eligible_backlog", "zero_proof_absent"}) {
		t.Fatalf("chromium sibling changed: %+v", got)
	}

	input = validInput()
	input.Items = []Item{validItem(0, "lightpanda", "monitor")}
	input.Lanes["lightpanda"] = withDeclared(input, "lightpanda")
	lane := input.Lanes["lightpanda"]
	lane.Telemetry.UtilizationP95Ratio = .850001
	input.Lanes["lightpanda"] = lane
	result = Evaluate(input)
	if got := result.Lanes["lightpanda"]; got.Decision != "defer" || got.DesiredConcurrency != 1 || got.SelectedItemIndex != nil || !sameReasons(got.Reasons, []string{"capacity_headroom_unsafe"}) {
		t.Fatalf("unsafe capacity decision = %+v", got)
	}
	if got := result.Lanes["chromium"]; got.Decision != "defer" || got.DesiredConcurrency != 1 {
		t.Fatalf("unsafe lightpanda changed chromium: %+v", got)
	}
}

func TestScaleUpAndFreezeNeverFallback(t *testing.T) {
	input := validInput()
	item := validItem(0, "chromium", "first_time")
	input.Items = []Item{item}
	lane := input.Lanes["chromium"]
	lane.Capacity.Inflight, lane.Capacity.Running, lane.Capacity.Admitted, lane.Capacity.Current = 1, 1, 1, 1
	input.Lanes["chromium"] = lane
	input.Lanes["chromium"] = withDeclared(input, "chromium")
	result := Evaluate(input)
	if got := result.Lanes["chromium"]; got.Decision != "defer" || got.DesiredConcurrency != 2 || !sameReasons(got.Reasons, []string{"scale_up_requested"}) {
		t.Fatalf("scale-up = %+v", got)
	}

	lane = input.Lanes["chromium"]
	lane.Telemetry.ErrorBudgetBurn = 1.000001
	input.Lanes["chromium"] = lane
	result = Evaluate(input)
	if got := result.Lanes["chromium"]; got.Decision != "freeze" || got.DesiredConcurrency != 1 || !sameReasons(got.Reasons, []string{"error_budget_exhausted"}) {
		t.Fatalf("error budget freeze = %+v", got)
	}
}

func TestAgingOverridesSustainedPriority(t *testing.T) {
	input := validInput()
	old := validItem(0, "lightpanda", "detail")
	old.EligibleSince = 100
	new := validItem(1, "lightpanda", "first_time")
	new.EligibleSince = 999
	input.Items = []Item{old, new}
	input.Lanes["lightpanda"] = withDeclared(input, "lightpanda")
	if got := Evaluate(input).Lanes["lightpanda"]; got.SelectedItemIndex == nil || *got.SelectedItemIndex != 0 {
		t.Fatalf("age override did not select oldest: %+v", got)
	}
}

func TestStrictDecoderDoesNotReflectMalformedInput(t *testing.T) {
	for _, raw := range [][]byte{
		[]byte(`{"now":0,"now":0}`),
		[]byte(`{"now":NaN}`),
		[]byte(`{"now":0} trailing`),
		[]byte(`{"now":0,"url":"https://secret.example"}`),
		bytes.Repeat([]byte("x"), maxDocument+1),
	} {
		_, err := DecodeInput(raw)
		if err == nil || err.Error() != "invalid_input" || strings.Contains(err.Error(), "secret") {
			t.Fatalf("unsafe parser error %q for %q", err, raw[:minInt(len(raw), 40)])
		}
	}
}

func TestCanonicalBytesHasNoIdentifiers(t *testing.T) {
	result := Evaluate(validInput())
	data, err := CanonicalBytes(result)
	if err != nil || strings.Contains(string(data), "routing") || strings.Contains(string(data), "owner") {
		t.Fatalf("unsafe normalized output %q, %v", data, err)
	}
	var decoded Result
	if err := json.Unmarshal(data, &decoded); err != nil || !sameResult(result, decoded) {
		t.Fatalf("canonical output not stable: %v", err)
	}
}

func validInput() Input {
	lanes := map[string]Lane{}
	for _, name := range []string{"lightpanda", "chromium"} {
		lanes[name] = Lane{
			Lane: name, RoutingRevision: "route-1", PolicyRevision: "policy-1", QueueRevision: "queue-1", ConfigRevision: "config-1", CapabilityCensusRevision: "census-1", QueueShardID: "shard-1", RoutingEpoch: 1, EngineOwner: "owner-1",
			Capacity:  Capacity{Current: 1, Desired: 1, Inflight: 0, Admitted: 1, Running: 0, WarmFloor: 1, HardMax: 4, ScaleUpStep: 1, ScaleDownStep: 1},
			Service:   Service{Ready: true, Admission: "admitted"},
			Telemetry: Telemetry{ObservedAt: 1000, QueueOldestAge: 0, UtilizationP95Ratio: .85, HeadroomP05Ratio: .15, ErrorBudgetBurn: 1},
			Declared:  Declared{},
		}
	}
	return Input{Now: 1000, PolicyRevision: "policy-1", RoutingRevision: "route-1", QueueRevision: "queue-1", ConfigRevision: "config-1", CapabilityCensusRevision: "census-1", Items: []Item{}, Lanes: lanes}
}

func validItem(ordinal uint64, lane, priority string) Item {
	return Item{Ordinal: ordinal, WorkClass: "monitor", Priority: priority, Lane: lane, DueAt: 1000, EligibleSince: 1000,
		Assignment: Assignment{Backend: lane, AssignmentRevision: "route-1", ImmutableCopy: AssignmentCopy{Backend: lane, AssignmentRevision: "route-1"}},
		Queue:      Queue{RouteRevision: "route-1", ConfigRevision: "config-1", Epoch: 1, Owner: "owner-1", ClaimFence: ordinal + 1}, Admission: Admission{Verdict: "permit", PolicyRevision: "policy-1"}}
}

func withDeclared(input Input, name string) Lane {
	lane := input.Lanes[name]
	var count, oldest uint64
	for _, item := range input.Items {
		if item.Lane == name && item.DueAt <= input.Now && item.Admission.Verdict == "permit" {
			count++
			if age := input.Now - item.EligibleSince; age > oldest {
				oldest = age
			}
		}
	}
	lane.Declared = Declared{EligibleReady: count, OldestEligibleAge: oldest}
	return lane
}

func sameReasons(got, want []string) bool {
	return len(got) == len(want) && strings.Join(got, "|") == strings.Join(want, "|")
}
func sameResult(a, b Result) bool { return string(mustJSON(a)) == string(mustJSON(b)) }
func mustJSON(value any) []byte   { data, _ := json.Marshal(value); return data }
func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// osReadFile is a seam so the test stays local to these two conformance paths.
var osReadFile = func(path string) ([]byte, error) { return os.ReadFile(path) }
