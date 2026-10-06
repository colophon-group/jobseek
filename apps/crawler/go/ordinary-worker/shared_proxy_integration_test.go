package worker

import (
	"encoding/json"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func proxyFixtureMetadata(t *testing.T, raw string, proxy bool) string {
	t.Helper()
	if !proxy {
		return raw
	}
	var md map[string]any
	if json.Unmarshal([]byte(raw), &md) != nil {
		t.Fatal("invalid fixture metadata")
	}
	md["proxy"] = true
	body, err := json.Marshal(md)
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

func TestSharedProxyRuntimePreservesIndependentDetailsAndBrowserTransport(t *testing.T) {
	for _, provider := range []string{"dom", "api_sniffer", "inline", "sitemap", "eightfold", "phenom"} {
		c := map[string]string{"crawler_type": provider, "monitor_needs_browser": "0", "metadata": `{"proxy":true,"scraper_type":"json-ld","scraper_config":{"proxy":false}}`}
		if !runtimeUsesProxy(queue.Task{Kind: queue.Monitor, Config: c}) {
			t.Fatal("configured HTTP proxy not selected", provider)
		}
		if runtimeUsesProxy(queue.Task{Kind: queue.Scrape, Config: c}) {
			t.Fatal("monitor changed independent detail transport", provider)
		}
		c["monitor_needs_browser"] = "1"
		if runtimeUsesProxy(queue.Task{Kind: queue.Monitor, Config: c}) {
			t.Fatal("HTTP client granted browser proxy support", provider)
		}
	}
}
