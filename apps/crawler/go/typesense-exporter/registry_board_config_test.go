package main

import (
	"bytes"
	"encoding/json"
	"os"
	"testing"
)

func TestRegistryBoardPreparationMatchesPython(t *testing.T) {
	path := os.Getenv("REGISTRY_BOARD_TEST_FIXTURE")
	if path == "" {
		path = "testdata/registry_board_fixture.json"
	}
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Row            map[string]*string `json:"row"`
		Metadata       map[string]any     `json:"metadata"`
		MonitorBrowser bool               `json:"monitor_browser"`
		ScraperBrowser bool               `json:"scraper_browser"`
		Invalid        bool               `json:"invalid"`
		ThrottleKey    string             `json:"throttle_key"`
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if err = decoder.Decode(&cases); err != nil {
		t.Fatal(err)
	}
	routes, err := loadRegistryRoutes()
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		metadata, err := registryBoardMetadata(c.Row)
		if c.Invalid {
			if err == nil {
				t.Fatalf("accepted invalid config for %s", registryText(c.Row, "board_url"))
			}
			continue
		}
		if err != nil {
			t.Fatalf("%s: %v", registryText(c.Row, "board_url"), err)
		}
		actual, encodeErr := taxonomyCanonicalJSON(metadata, true)
		expected, expectedErr := taxonomyCanonicalJSON(c.Metadata, true)
		if encodeErr != nil || expectedErr != nil || !bytes.Equal(actual, expected) {
			t.Fatalf("metadata differs for %s: %#v != %#v", registryText(c.Row, "board_url"), metadata, c.Metadata)
		}
		mon := registryMonitorBrowser(registryText(c.Row, "monitor_type"), metadata)
		scrType, _ := metadata["scraper_type"].(string)
		scrConfig, _ := metadata["scraper_config"].(map[string]any)
		scr := registryScraperBrowser(scrType, scrConfig, routes)
		if mon != c.MonitorBrowser || scr != c.ScraperBrowser {
			t.Fatalf("routing differs for %s: %v/%v expected %v/%v", registryText(c.Row, "board_url"), mon, scr, c.MonitorBrowser, c.ScraperBrowser)
		}
		if key := registryThrottleKey(registryText(c.Row, "monitor_type"), registryText(c.Row, "board_url"), metadata, routes); key != c.ThrottleKey {
			t.Fatalf("throttle differs for %s: %q expected %q", registryText(c.Row, "board_url"), key, c.ThrottleKey)
		}
	}
	t.Logf("matched %d board configurations", len(cases))
}
