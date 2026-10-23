package queue

import (
	"encoding/json"
	"testing"
)

func TestRenderedResourceNoneKeepsProfileAndCanonicalFence(t *testing.T) {
	c := domMonitorConfig()
	c["monitor_needs_browser"] = "1"
	md := map[string]any{"render": true, "url_filter": "/jobs/", "scraper_type": "json-ld"}
	encode := func() { b, _ := json.Marshal(md); c["metadata"] = string(b) }
	encode()
	base, e := InspectRichMonitor(profileBoardID, c)
	if e != nil {
		t.Fatal(e)
	}
	md["resource_policy"] = "none"
	encode()
	p, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || p.Profile != base.Profile || MonitorWorker(p) != Browser || p.EffectiveConfigSHA256 == base.EffectiveConfigSHA256 {
		t.Fatal("explicit resource setting lost profile/canonical fence", e)
	}
	_, options, e := RenderedDOMMonitorOptions(c)
	if e != nil || options["resource_policy"] != "none" {
		t.Fatal("original navigation control lost", e)
	}
	md["resource_policy"] = "auto"
	encode()
	auto, e := InspectRichMonitor(profileBoardID, c)
	_, options, optionsErr := RenderedDOMMonitorOptions(c)
	if e != nil || optionsErr != nil || auto.Profile != p.Profile ||
		auto.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 || options["resource_policy"] != "none" {
		t.Fatal("qualified auto normalization lost original policy binding", e, optionsErr)
	}
	md["resource_policy"] = "lean"
	encode()
	lean, e := InspectRichMonitor(profileBoardID, c)
	_, options, optionsErr = RenderedDOMMonitorOptions(c)
	if e != nil || optionsErr != nil || lean.Profile != p.Profile ||
		lean.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 || lean.EffectiveConfigSHA256 == auto.EffectiveConfigSHA256 || options["resource_policy"] != "none" {
		t.Fatal("pinned lean normalization lost the original policy binding", e, optionsErr)
	}
	for _, value := range []any{"aggressive", false, 1, map[string]any{}, []any{}, " none"} {
		md["resource_policy"] = value
		encode()
		if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
			t.Fatal("unimplemented policy accepted", value)
		}
	}
	for _, scraper := range []string{"dom", "json-ld"} {
		detail := renderedDetailConfig(scraper)
		original, e := InspectRenderedDetail(jsonldBoardID, detail, "https://jobs.example.net/42", Browser)
		if e != nil {
			t.Fatal(e)
		}
		var metadata map[string]any
		json.Unmarshal([]byte(detail["metadata"]), &metadata)
		parser := metadata["scraper_config"].(map[string]any)
		parser["resource_policy"] = "none"
		b, _ := json.Marshal(metadata)
		detail["metadata"] = string(b)
		got, e := InspectRenderedDetail(jsonldBoardID, detail, original.SourceURL, Browser)
		if e != nil || got.Profile != original.Profile || got.EffectiveBoardSHA256 == original.EffectiveBoardSHA256 {
			t.Fatal("detail canonical fence lost", scraper, e)
		}
		for _, policy := range []string{"auto", "lean", "aggressive"} {
			parser["resource_policy"] = policy
			b, _ = json.Marshal(metadata)
			detail["metadata"] = string(b)
			bound, e := InspectRenderedDetail(jsonldBoardID, detail, original.SourceURL, Browser)
			if scraper == "dom" && policy != "aggressive" {
				if e != nil || bound.Profile != original.Profile || bound.EffectiveBoardSHA256 == got.EffectiveBoardSHA256 || bound.DOMConfig["resource_policy"] != policy {
					t.Fatal("qualified DOM detail policy lost its original binding", policy, e)
				}
			} else if e == nil {
				t.Fatal("unqualified detail resource policy accepted", policy)
			}
		}
	}
}
