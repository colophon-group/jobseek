package queue

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func cmaHTTPMonitorFixture(t *testing.T) (string, map[string]string) {
	t.Helper()
	body, err := os.ReadFile("testdata/cma_http_detail_monitor_board.json")
	if err != nil {
		t.Fatal(err)
	}
	var f struct {
		ID     string            `json:"id"`
		Config map[string]string `json:"config"`
	}
	if json.Unmarshal(body, &f) != nil {
		t.Fatal("invalid CMA fixture")
	}
	return f.ID, f.Config
}

func TestCMAHTTPDetailPreservesQualifiedRenderedMonitor(t *testing.T) {
	id, current := cmaHTTPMonitorFixture(t)
	md, err := richProfileMetadata(current)
	if err != nil || selfIdentifyingDOMSource(current, md) || !selfIdentifyingDOMMonitorSource(current, md) {
		t.Fatal("fixture must reproduce the detail-only fingerprint transition", err)
	}
	legacy := cloneConfig(current)
	legacyMD := make(map[string]json.RawMessage, len(md))
	for key, value := range md {
		legacyMD[key] = value
	}
	legacyMD["scraper_config"] = cmaHTTPDetailMonitor.Reverse
	body, _ := json.Marshal(legacyMD)
	legacy["metadata"], legacy["scraper_needs_browser"] = string(body), "1"
	if !selfIdentifyingDOMSource(legacy, legacyMD) {
		t.Fatal("original frozen monitor contract no longer matches")
	}
	a, err := InspectRichMonitor(id, current)
	if err != nil || a.Profile != domRenderedMonitorProfile || MonitorWorker(a) != Browser {
		t.Fatal("HTTP detail lost prior monitor authority", err)
	}
	b, err := InspectRichMonitor(id, legacy)
	if err != nil || b.Profile != a.Profile || a.EffectiveConfigSHA256 == b.EffectiveConfigSHA256 {
		t.Fatal("ownership must bind the actual changed detail config", err)
	}
	listing, options, err := RenderedDOMMonitorOptions(current)
	oldListing, oldOptions, oldErr := RenderedDOMMonitorOptions(legacy)
	if err != nil || oldErr != nil || !listing.RequireNonEmpty || !reflect.DeepEqual(listing, oldListing) || !reflect.DeepEqual(options, oldOptions) {
		t.Fatal("qualified navigation, parser or empty-inventory guard changed", err, oldErr)
	}
	if skip, err := MonitorSkipsSSL(current); err != nil || skip {
		t.Fatal("unchanged direct transport policy lost qualification", err)
	}
}

func TestCMAHTTPMonitorRejectsUnqualifiedVariations(t *testing.T) {
	id, base := cmaHTTPMonitorFixture(t)
	changes := map[string]func(map[string]string, map[string]json.RawMessage){
		"board": func(c map[string]string, _ map[string]json.RawMessage) { c["board_slug"] = "other" },
		"origin": func(c map[string]string, _ map[string]json.RawMessage) {
			c["board_url"] = "https://other.example/jobs/"
		},
		"monitor lane": func(c map[string]string, _ map[string]json.RawMessage) { c["monitor_needs_browser"] = "0" },
		"detail lane":  func(c map[string]string, _ map[string]json.RawMessage) { c["scraper_needs_browser"] = "1" },
		"detail type": func(_ map[string]string, m map[string]json.RawMessage) {
			m["scraper_type"] = json.RawMessage(`"json-ld"`)
		},
		"detail parser": func(_ map[string]string, m map[string]json.RawMessage) {
			m["scraper_config"] = json.RawMessage(`{"render":false}`)
		},
		"navigation": func(_ map[string]string, m map[string]json.RawMessage) { m["wait"] = json.RawMessage(`"load"`) },
		"proxy":      func(_ map[string]string, m map[string]json.RawMessage) { m["proxy"] = json.RawMessage(`true`) },
	}
	for name, change := range changes {
		t.Run(name, func(t *testing.T) {
			c := cloneConfig(base)
			md, _ := richProfileMetadata(c)
			change(c, md)
			body, _ := json.Marshal(md)
			c["metadata"] = string(body)
			if selfIdentifyingDOMMonitorSource(c, md) {
				t.Fatal("unqualified config inherited original source permission", id)
			}
		})
	}
}

func TestCMAHTTPMonitorContractMatchesWorkerRouteCutover(t *testing.T) {
	body, err := os.ReadFile("../typesense-exporter/provider-cutover/browser-http-routes.json")
	if err != nil {
		t.Fatal(err)
	}
	var targets []map[string]any
	var current map[string]any
	if json.Unmarshal(body, &targets) != nil || json.Unmarshal(cmaHTTPDetailMonitorContract, &current) != nil {
		t.Fatal("invalid committed route contract")
	}
	for _, target := range targets {
		if target["slug"] == current["slug"] {
			if !reflect.DeepEqual(target, current) {
				t.Fatal("monitor and deploy route contracts diverged")
			}
			return
		}
	}
	t.Fatal("CMA worker route contract missing")
}
