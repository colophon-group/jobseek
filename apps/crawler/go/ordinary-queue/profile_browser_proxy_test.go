package queue

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestConfiguredBrowserProxyDetailsBindIndependentOriginalFamilies(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld", "embedded", "nextdata"} {
		t.Run(scraper, func(t *testing.T) {
			config := renderedDetailConfig(scraper)
			options := map[string]any{"render": true, "proxy": true}
			if scraper == "dom" {
				options["steps"] = []any{map[string]any{"tag": "h1", "field": "title"}}
			}
			if scraper == "embedded" || scraper == "nextdata" {
				options["path"] = "job"
				options["fields"] = map[string]any{"title": "title", "description": "description"}
			}
			if scraper == "embedded" {
				options["script_id"] = "fixture-data"
			}
			body, _ := json.Marshal(map[string]any{"scraper_type": scraper, "scraper_config": options})
			config["metadata"] = string(body)
			original := config["metadata"]
			profile, err := InspectRenderedDetail(jsonldBoardID, config, "https://jobs.example.net/42", Browser)
			if err != nil || !profile.BrowserProxy || detailWorker(profile.Profile) != Browser || config["metadata"] != original {
				t.Fatal("configured proxy document admission lost original family or worker", err)
			}
			owner, err := inspectDetailOwnership(jsonldBoardID, config)
			if err != nil || !owner.BrowserProxy || owner.EffectiveBoardSHA256 != profile.EffectiveBoardSHA256 {
				t.Fatal("configured proxy detail ownership differs", err)
			}
			authority := &Authority{ownership: &OwnershipPlan{document: ownershipDocument{Details: []ownershipDetail{{BoardID: jsonldBoardID, Worker: Browser, Config: config, Profile: profile.Profile}}}}}
			if !authority.RequiresProxyHTTP() {
				t.Fatal("browser detail omitted shared startup pool")
			}
			for _, attempts := range []string{"1", "3", "5"} {
				config["metadata"] = strings.Replace(original, `"proxy":true`, `"proxy":true,"transport_attempts":`+attempts, 1)
				if candidate, err := InspectRenderedDetail(jsonldBoardID, config, profile.SourceURL, Browser); err != nil || !candidate.BrowserProxy || candidate.EffectiveBoardSHA256 == profile.EffectiveBoardSHA256 {
					t.Fatal("proxy retry bound lost original binding", err)
				}
			}
			for _, attempts := range []string{"0", "6", "2.5", "true", "null"} {
				config["metadata"] = strings.Replace(original, `"proxy":true`, `"proxy":true,"transport_attempts":`+attempts, 1)
				if _, err := InspectRenderedDetail(jsonldBoardID, config, profile.SourceURL, Browser); err == nil {
					t.Fatal("invalid proxy retry bound admitted")
				}
			}
			config["metadata"] = strings.Replace(original, `"proxy":true`, `"proxy":false`, 1)
			direct, err := InspectRenderedDetail(jsonldBoardID, config, profile.SourceURL, Browser)
			if err != nil || direct.BrowserProxy || direct.EffectiveBoardSHA256 == profile.EffectiveBoardSHA256 {
				t.Fatal("proxy policy lost its original configuration binding", err)
			}
			config["metadata"] = strings.Replace(original, `"proxy":true`, `"proxy":true,"skip_ssl":true`, 1)
			if _, err := InspectRenderedDetail(jsonldBoardID, config, profile.SourceURL, Browser); err == nil {
				t.Fatal("proxy admission enabled unimplemented TLS controls")
			}
			config["metadata"] = strings.Replace(original, `"proxy":true`, `"proxy":true,"proxy":false`, 1)
			if _, err := InspectRenderedDetail(jsonldBoardID, config, profile.SourceURL, Browser); err == nil {
				t.Fatal("duplicate proxy policy admitted")
			}
		})
	}
}

func TestConfiguredBrowserProxyMonitorKeepsIndependentDetailAndCanonicalBinding(t *testing.T) {
	config := domMonitorConfig()
	config["monitor_needs_browser"] = "1"
	config["scraper_needs_browser"] = "0"
	config["metadata"] = `{"render":true,"proxy":true,"url_filter":{"include":"/jobs/"},"scraper_type":"json-ld"}`
	profile, err := InspectRichMonitor(profileBoardID, config)
	if err != nil || !profile.BrowserProxy || MonitorWorker(profile) != Browser {
		t.Fatal("configured proxy monitor was not compiled", err)
	}
	_, options, err := RenderedDOMMonitorOptions(config)
	if err != nil || options["proxy"] != true {
		t.Fatal("proxy transport disappeared from navigation", err)
	}
	owner, err := inspectDetailOwnership(profileBoardID, config)
	if err != nil || owner.BrowserProxy || ProfileRequiresProxy(owner.Profile) {
		t.Fatal("monitor proxy changed independent detail transport", err)
	}
	authority := &Authority{ownership: &OwnershipPlan{document: ownershipDocument{Members: []ownershipMember{{BoardID: profileBoardID, Worker: Browser, Config: config, Profile: profile.Profile}}}}}
	if !authority.RequiresProxyHTTP() {
		t.Fatal("browser monitor omitted shared startup pool")
	}
	config["metadata"] = strings.Replace(config["metadata"], `"proxy":true`, `"proxy":false`, 1)
	direct, err := InspectRichMonitor(profileBoardID, config)
	if err != nil || direct.BrowserProxy || direct.EffectiveConfigSHA256 == profile.EffectiveConfigSHA256 {
		t.Fatal("monitor proxy policy lost original canonical binding", err)
	}
}

func TestRealConfiguredBrowserProxyDetailColdRetirement(t *testing.T) {
	for _, row := range []struct{ scraper, parser string }{
		{"dom", `{"render":true,"proxy":true,"transport_attempts":2,"steps":[{"tag":"h1","field":"title"}]}`},
		{"json-ld", `{"render":true,"proxy":true,"transport_attempts":2}`},
		{"nextdata", `{"render":true,"proxy":true,"transport_attempts":2,"path":"job","fields":{"title":"title","description":"description"}}`},
		{"embedded", `{"render":true,"proxy":true,"transport_attempts":2,"script_id":"fixture-data","path":"job","fields":{"title":"title","description":"description"}}`},
	} {
		t.Run(row.scraper, func(t *testing.T) {
			metadata := `{"scraper_type":"` + row.scraper + `","scraper_config":` + row.parser + `}`
			testIndependentDetailColdRetirement(t, func(t *testing.T) firstOwnerFixture {
				return firstIndependentDetailFixture(t, metadata, "https://careers.example.net/job/123", "careers.example.net", Browser)
			})
		})
	}
}
