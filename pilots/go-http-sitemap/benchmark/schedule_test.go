package benchmark

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"testing"
)

func TestCommittedScheduleIsBalancedAndBounded(t *testing.T) {
	type pair struct {
		ID      string `json:"id"`
		Profile string `json:"profile"`
		First   string `json:"first"`
	}
	type schedule struct {
		SchemaVersion   int    `json:"schema_version"`
		Seed            int    `json:"seed"`
		CooldownSeconds int    `json:"cooldown_seconds"`
		Pairs           []pair `json:"pairs"`
	}
	contents, err := os.ReadFile("schedule.json")
	if err != nil {
		t.Fatal(err)
	}
	var got schedule
	decoder := json.NewDecoder(bytes.NewReader(contents))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&got); err != nil {
		t.Fatal(err)
	}
	if got.SchemaVersion != 1 || got.Seed != 8648 || got.CooldownSeconds != 8 || len(got.Pairs) != 18 {
		t.Fatalf("schedule header=%+v", got)
	}
	wantRepetitions := map[string]int{"c5": 6, "c12": 6, "c16": 6}
	totals := make(map[string]int)
	goFirst := make(map[string]int)
	positions := make(map[string]map[int]int)
	for profile := range wantRepetitions {
		positions[profile] = make(map[int]int)
	}
	for index, pair := range got.Pairs {
		if pair.ID != fmt.Sprintf("p%02d", index+1) {
			t.Fatalf("pair %d id=%q", index+1, pair.ID)
		}
		if _, ok := wantRepetitions[pair.Profile]; !ok || pair.First != "go" && pair.First != "python" {
			t.Fatalf("invalid pair=%+v", pair)
		}
		totals[pair.Profile]++
		if pair.First == "go" {
			goFirst[pair.Profile]++
		}
	}
	for profile, want := range wantRepetitions {
		if totals[profile] != want || goFirst[profile] != want/2 {
			t.Fatalf("profile %s totals=%d go-first=%d", profile, totals[profile], goFirst[profile])
		}
	}
	for block := 0; block < 6; block++ {
		seen := make(map[string]bool)
		for position, pair := range got.Pairs[block*3 : block*3+3] {
			seen[pair.Profile] = true
			positions[pair.Profile][position]++
		}
		if len(seen) != len(wantRepetitions) {
			t.Fatalf("block %d profiles=%v", block+1, seen)
		}
	}
	for profile := range wantRepetitions {
		for position := 0; position < 3; position++ {
			if positions[profile][position] != 2 {
				t.Fatalf("profile %s position %d count=%d", profile, position+1, positions[profile][position])
			}
		}
		for window := 0; window < 3; window++ {
			goFirstInWindow := 0
			for _, pair := range got.Pairs[window*6 : window*6+6] {
				if pair.Profile == profile && pair.First == "go" {
					goFirstInWindow++
				}
			}
			if goFirstInWindow != 1 {
				t.Fatalf("profile %s window %d go-first=%d", profile, window+1, goFirstInWindow)
			}
		}
	}
}
