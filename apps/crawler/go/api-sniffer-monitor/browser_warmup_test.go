package apisniffer

import (
	"encoding/json"
	"testing"
)

func TestBrowserWarmupDoesNotBecomeHTTPAnnotation(t *testing.T) {
	metadata := map[string]any{"browser": true, "api_url": "https://jobs.example/api", "json_path": "jobs", "url_field": "url", "warmup_url": "https://jobs.example/careers"}
	raw, _ := json.Marshal(metadata)
	o, err := BrowserReplayOptionsFromMetadata("https://jobs.example/jobs", string(raw))
	if err != nil || o.WarmupURL != metadata["warmup_url"] || o.BootstrapURL != "https://jobs.example/jobs" {
		t.Fatal("warmup changed the canonical bootstrap", err)
	}
	metadata["browser"] = false
	raw, _ = json.Marshal(metadata)
	if _, err := OptionsFromMetadata("https://jobs.example/jobs", string(raw)); err == nil {
		t.Fatal("HTTP route silently discarded warmup")
	}
	metadata["browser"] = true
	for _, warmup := range []any{nil, 1, "", "https://foreign.example/", "http://jobs.example/", "https://user@jobs.example/", "https://jobs.example/#fragment"} {
		metadata["warmup_url"] = warmup
		raw, _ = json.Marshal(metadata)
		if _, err := BrowserReplayOptionsFromMetadata("https://jobs.example/jobs", string(raw)); err == nil {
			t.Fatal("unbound browser warmup admitted")
		}
	}
}
