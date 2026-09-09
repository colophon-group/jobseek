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
	if got.SchemaVersion != 1 || got.Seed != 8648 || got.CooldownSeconds != 8 || len(got.Pairs) != 30 {
		t.Fatalf("schedule header=%+v", got)
	}
	wantRepetitions := map[string]int{"c2": 4, "c4": 4, "c5": 6, "c8": 4, "c12": 6, "c16": 6}
	totals := make(map[string]int)
	goFirst := make(map[string]int)
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
}
