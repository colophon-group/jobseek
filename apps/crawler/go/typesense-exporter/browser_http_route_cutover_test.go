package main

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestBrowserHTTPRouteContractsBindPublishedConfigurationAndReverse(t *testing.T) {
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
	boards := map[string][]string{}
	for _, row := range rows[1:] {
		boards[row[h["board_slug"]]] = row
	}
	for _, target := range browserHTTPRouteTargets() {
		t.Run(target.Slug, func(t *testing.T) {
			row := boards[target.Slug]
			if row == nil || row[h["board_url"]] != target.BoardURL {
				t.Fatal("published board identity differs")
			}
			var forward map[string]any
			if json.Unmarshal([]byte(row[h["scraper_config"]]), &forward) != nil || !target.StaticConfigs.matches(row[h["scraper_type"]], forward, false) {
				t.Fatal("published HTTP config differs")
			}
			if !target.StaticConfigs.matches(target.LegacyType, target.StaticConfigs.Reverse, true) || target.StaticConfigs.matches(target.LegacyType, forward, true) || target.StaticConfigs.matches("api_sniffer", forward, false) {
				t.Fatal("reverse config or type binding differs")
			}
			forward["unproved"] = true
			if target.StaticConfigs.matches(target.LegacyType, forward, false) {
				t.Fatal("unproved config acquired route authority")
			}
		})
	}
}
