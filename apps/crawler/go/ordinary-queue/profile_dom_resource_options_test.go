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
	for _, policy := range []string{"lean", "aggressive", "unknown"} {
		clone := cloneConfig(config)
		clone["metadata"] = strings.Replace(clone["metadata"], `"auto"`, `"`+policy+`"`, 1)
		if _, e := InspectRichMonitor(p.BoardID, clone); e == nil {
			t.Fatal("unqualified resource policy accepted")
		}
	}
	static := cloneConfig(config)
	static["monitor_needs_browser"] = "0"
	static["metadata"] = strings.Replace(static["metadata"], `"render":true`, `"render":false`, 1)
	if _, e := InspectRichMonitor(p.BoardID, static); e == nil {
		t.Fatal("rendered resource proof granted static admission")
	}
}
