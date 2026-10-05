package queue

import (
	"encoding/json"
	"errors"
	"testing"
)

func TestRenderedDOMMonitorBindsBrowserInventory(t *testing.T) {
	c := domMonitorConfig()
	c["monitor_needs_browser"] = "1"
	c["metadata"] = `{"render":true,"wait":"networkidle","timeout":30000,"url_filter":{"include":"/jobs/"},"scraper_type":"json-ld"}`
	p, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || MonitorWorker(p) != Browser {
		t.Fatal(p, e)
	}
	md, _ := richProfileMetadata(c)
	stable, _ := stableJSONLDConfig(c, md)
	doc := testOwnershipDocument(t)
	doc.Members = []ownershipMember{{profileBoardID, p.CompanyID, p.Domain, Monitor, Browser, p.Profile, p.EffectiveConfigSHA256, stable}}
	body, hash := testOwnershipBody(t, doc)
	if _, e := decodeOwnership(body, hash); e != nil {
		t.Fatal(e)
	}
	doc.Members[0].Worker = Simple
	body, hash = testOwnershipBody(t, doc)
	if _, e := decodeOwnership(body, hash); !errors.Is(e, ErrAuthorityLost) {
		t.Fatal("wrong monitor worker accepted", e)
	}
	c["metadata"] = `{"url_filter":{"include":"/jobs/"},"timeout":30000,"wait":"networkidle","render":true,"scraper_type":"json-ld"}`
	q, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || q.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 {
		t.Fatal("canonical order changed", e)
	}
	for key, value := range map[string]any{"actions": []any{map[string]any{"action": "click"}}, "pagination": map[string]any{"selector": "a.next"}, "browser_backend": "chromium", "proxy": true, "request_headers": map[string]any{"Accept": "text/html"}, "wait": "bad", "timeout": true, "render": false, "encoding": "utf-8", "transport_attempts": 3, "unknown": true} {
		var md map[string]any
		json.Unmarshal([]byte(c["metadata"]), &md)
		md[key] = value
		b, _ := json.Marshal(md)
		clone := cloneConfig(c)
		clone["metadata"] = string(b)
		if _, e := InspectRichMonitor(profileBoardID, clone); e == nil {
			t.Fatal("unsupported monitor admitted", key)
		}
	}
}
