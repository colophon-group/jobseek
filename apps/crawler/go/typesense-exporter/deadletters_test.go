package main

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"
)

func deadletterOracle(t *testing.T) (deadletterSnapshot, deadletterReport) {
	t.Helper()
	content, err := os.ReadFile("testdata/deadletter_fixture.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Input    deadletterSnapshot `json:"input"`
		Expected deadletterReport   `json:"expected"`
	}
	if err := json.Unmarshal(content, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture.Input, fixture.Expected
}
func TestDeadletterPythonOracle(t *testing.T) {
	input, expected := deadletterOracle(t)
	result, err := classifyDeadletterSnapshot(input)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(result, expected) {
		for i := range expected.Entries {
			if !reflect.DeepEqual(result.Entries[i], expected.Entries[i]) {
				t.Errorf("entry %d: got %+v want %+v", i, result.Entries[i], expected.Entries[i])
			}
		}
		t.Fatalf("report differs from Python oracle")
	}
}
func TestDeadletterMalformedScoresAndEmptyReport(t *testing.T) {
	for _, score := range []float64{math.Inf(1), math.Inf(-1), math.NaN(), 253402300800, -62135596801} {
		if _, err := parseDeadletter("simple", "monitor|x|x", score); err == nil {
			t.Errorf("accepted %v", score)
		}
	}
	result, err := classifyDeadletterSnapshot(deadletterSnapshot{})
	if err != nil || result.Total != 0 || len(result.Counts) != 2 || result.Entries == nil || result.Outcomes == nil {
		t.Fatalf("invalid empty report: %+v %v", result, err)
	}
}
