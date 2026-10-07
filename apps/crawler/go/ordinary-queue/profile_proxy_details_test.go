package queue

import (
	"encoding/json"
	"strings"
	"testing"
)

var proxyDetailCases = []struct{ scraper, metadata, profile string }{
	{"dom", `{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}]}}`, domProxyDetailProfile},
	{"json-ld", `{"scraper_type":"json-ld","scraper_config":{"defaults":{"language":"en"}}}`, jsonldProxyDetailProfile},
	{"api_sniffer", `{"scraper_type":"api_sniffer","scraper_config":{"api_url":"https://api.example.net/{id}","fields":{"title":"title","description":"description"}}}`, httpAPIProxyDetailProfile},
}

func proxyDetailMetadata(t *testing.T, raw string, enabled bool) string {
	t.Helper()
	var md map[string]any
	if json.Unmarshal([]byte(raw), &md) != nil {
		t.Fatal("invalid detail fixture")
	}
	md["scraper_config"].(map[string]any)["proxy"] = enabled
	body, e := json.Marshal(md)
	if e != nil {
		t.Fatal(e)
	}
	return string(body)
}

func TestProxyDetailProfilesKeepOriginalBindingAndIndependentMonitor(t *testing.T) {
	for _, row := range proxyDetailCases {
		t.Run(row.scraper, func(t *testing.T) {
			c := profileConfig()
			c["metadata"] = row.metadata
			direct, e := inspectDetailOwnership(profileBoardID, c)
			if e != nil {
				t.Fatal(e)
			}
			c["metadata"] = proxyDetailMetadata(t, row.metadata, true)
			original := c["metadata"]
			p, e := inspectDetailOwnership(profileBoardID, c)
			if e != nil || p.Profile != row.profile || !ProfileRequiresProxy(p.Profile) || ProfileRequiresProxy(direct.Profile) || p.EffectiveBoardSHA256 == direct.EffectiveBoardSHA256 || c["metadata"] != original || p.Domain != "*" {
				t.Fatal("proxy escaped independent immutable detail binding", p, e)
			}
			c["metadata"] = proxyDetailMetadata(t, row.metadata, false)
			p, e = inspectDetailOwnership(profileBoardID, c)
			if e != nil || p.Profile != direct.Profile || ProfileRequiresProxy(p.Profile) {
				t.Fatal("explicit direct setting acquired proxy transport", e)
			}
			c["metadata"] = strings.Replace(original, `"proxy":true`, `"proxy":true,"proxy":false`, 1)
			if _, e := inspectDetailOwnership(profileBoardID, c); e == nil {
				t.Fatal("duplicate transport field admitted")
			}
			c["metadata"] = original
			c["scraper_needs_browser"] = "1"
			if _, e := inspectDetailOwnership(profileBoardID, c); e == nil {
				t.Fatal("HTTP proxy admitted browser details")
			}
		})
	}
}

func TestRealSharedProxyDetailsOwnershipAndColdRetirement(t *testing.T) {
	for _, row := range proxyDetailCases {
		md := proxyDetailMetadata(t, row.metadata, true)
		t.Run(row.scraper+"/ownership", func(t *testing.T) {
			testIndependentDetailOwnsPosting(t, firstIndependentDetailFixture(t, md, "https://careers.example.net/job/123", "careers.example.net"), row.profile, "careers.example.net")
		})
		t.Run(row.scraper+"/cold-retirement", func(t *testing.T) {
			testIndependentDetailColdRetirement(t, func(t *testing.T) firstOwnerFixture {
				return firstIndependentDetailFixture(t, md, "https://careers.example.net/job/123", "careers.example.net")
			})
		})
	}
}
