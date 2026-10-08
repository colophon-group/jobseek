package apisniffer

import (
	"encoding/csv"
	"os"
	"reflect"
	"testing"
)

func TestTenthProviderOptionsCoverCurrentFourProviderConfigurations(t *testing.T) {
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	h := map[string]int{}
	for i, key := range rows[0] {
		h[key] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[h["monitor_type"]]
		if provider != "intervieweb" && provider != "typify" && provider != "universia" && provider != "talentreef" {
			continue
		}
		metadata := row[h["monitor_config"]]
		o, err := TenthProviderOptionsFromMetadata(provider, row[h["board_url"]], metadata)
		if err != nil || o.Profile() == "" || !o.ResourceMatches(o.ListingURL()) || o.ResourceMatches("https://foreign.example/api/vacancies") || o.ResourceMatches(o.Origin+"/admin") {
			t.Fatal("configured provider identity or resource boundary lost", row[h["board_slug"]], err)
		}
		counts[provider]++
	}
	if !reflect.DeepEqual(counts, map[string]int{"intervieweb": 3, "typify": 3, "universia": 1, "talentreef": 2}) {
		t.Fatal("four-provider configuration fixture changed", counts)
	}
}
