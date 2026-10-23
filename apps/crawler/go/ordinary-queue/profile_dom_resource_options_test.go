package queue

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestDOMAutoResourceNormalizationRetainsImmutableBinding(t *testing.T) {
	config := domMonitorConfig()
	config["monitor_needs_browser"] = "1"
	config["scraper_needs_browser"] = "0"
	config["metadata"] = `{"render":true,"resource_policy":"auto","bot_protection":false,"url_filter":"/jobs/","scraper_type":"skip"}`
	p, e := InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
	if e != nil {
		t.Fatal(e)
	}
	_, options, e := RenderedDOMMonitorOptions(config)
	if e != nil || options["resource_policy"] != "none" {
		t.Fatal("inspected engine route rejected", e)
	}
	if _, exists := options["bot_protection"]; exists {
		t.Fatal("recon flag reached execution plan")
	}
	if !strings.Contains(config["metadata"], `"resource_policy":"auto"`) {
		t.Fatal("original config mutated")
	}
	clone := cloneConfig(config)
	clone["metadata"] = strings.Replace(clone["metadata"], `"bot_protection":false`, `"bot_protection":true`, 1)
	q, e := InspectRichMonitor(p.BoardID, clone)
	if e != nil || p.EffectiveConfigSHA256 == q.EffectiveConfigSHA256 {
		t.Fatal("original recon flag disappeared from ownership binding", e)
	}
	for _, raw := range []string{`null`, `0`, `"false"`, `[]`, `{}`} {
		clone := cloneConfig(config)
		var md map[string]json.RawMessage
		json.Unmarshal([]byte(config["metadata"]), &md)
		md["bot_protection"] = json.RawMessage(raw)
		b, _ := json.Marshal(md)
		clone["metadata"] = string(b)
		if _, e := InspectRichMonitor(p.BoardID, clone); e == nil {
			t.Fatal("malformed recon flag accepted")
		}
	}
	for _, policy := range []string{"aggressive", "unknown"} {
		clone := cloneConfig(config)
		clone["metadata"] = strings.Replace(clone["metadata"], `"auto"`, `"`+policy+`"`, 1)
		if _, e := InspectRichMonitor(p.BoardID, clone); e == nil {
			t.Fatal("unqualified resource policy accepted")
		}
	}
	lean := cloneConfig(config)
	lean["metadata"] = strings.Replace(lean["metadata"], `"auto"`, `"lean"`, 1)
	leanProfile, err := InspectRichMonitor(p.BoardID, lean)
	_, leanOptions, optionsErr := RenderedDOMMonitorOptions(lean)
	if err != nil || optionsErr != nil || leanOptions["resource_policy"] != "none" || leanProfile.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("pinned font/media exclusion or original policy binding lost", err, optionsErr)
	}
	static := cloneConfig(config)
	static["monitor_needs_browser"] = "0"
	static["metadata"] = strings.Replace(static["metadata"], `"render":true`, `"render":false`, 1)
	if _, e := InspectRichMonitor(p.BoardID, static); e == nil {
		t.Fatal("rendered resource proof granted static admission")
	}
}

func TestDOMDetailResourcePoliciesKeepFullBindingAndParserScope(t *testing.T) {
	hashes := map[string]bool{}
	for _, resourcePolicy := range []string{"none", "lean", "auto"} {
		config := renderedDetailConfig("dom")
		var metadata map[string]any
		if json.Unmarshal([]byte(config["metadata"]), &metadata) != nil {
			t.Fatal("configured parser unavailable")
		}
		metadata["scraper_config"].(map[string]any)["resource_policy"] = resourcePolicy
		body, _ := json.Marshal(metadata)
		config["metadata"] = string(body)
		profile, err := InspectRenderedDetail(jsonldBoardID, config, "https://jobs.example.net/42", Browser)
		if err != nil || profile.DOMConfig["resource_policy"] != resourcePolicy || config["metadata"] != string(body) {
			t.Fatal("original detail policy escaped its immutable binding", err)
		}
		if hashes[profile.EffectiveBoardSHA256] {
			t.Fatal("different original detail policies reused ownership")
		}
		hashes[profile.EffectiveBoardSHA256] = true
	}
	for _, scraper := range []string{"dom", "json-ld"} {
		for _, resourcePolicy := range []any{"aggressive", "unknown", true, 1} {
			config := renderedDetailConfig(scraper)
			var metadata map[string]any
			json.Unmarshal([]byte(config["metadata"]), &metadata)
			metadata["scraper_config"].(map[string]any)["resource_policy"] = resourcePolicy
			body, _ := json.Marshal(metadata)
			config["metadata"] = string(body)
			if _, err := InspectRenderedDetail(jsonldBoardID, config, "https://jobs.example.net/42", Browser); err == nil {
				t.Fatal("unqualified detail policy admitted", scraper, resourcePolicy)
			}
		}
	}
}
