package queue

import (
	"encoding/json"
	"os"
	"testing"
)

func TestRetainedRichProviderConfigsMatchOriginalRequestsAndEnrichment(t *testing.T) {
	body, e := os.ReadFile("testdata/python_retained_rich_config.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Slug, Provider string
		BoardURL       string `json:"board_url"`
		Metadata       json.RawMessage
		Requests       []struct{ Method, URL string }
		Enrich         any
	}
	if json.Unmarshal(body, &cases) != nil || len(cases) != 6 {
		t.Fatal("original current variant corpus unavailable")
	}
	for _, c := range cases {
		t.Run(c.Slug, func(t *testing.T) {
			config := profileConfig()
			config["crawler_type"], config["board_url"], config["metadata"] = c.Provider, c.BoardURL, string(c.Metadata)
			p, e := InspectRichMonitor(profileBoardID, config)
			if e != nil {
				t.Fatal("current variant not admitted", e)
			}
			if len(c.Requests) != 1 || c.Requests[0].Method != "GET" || c.Requests[0].URL != p.Endpoint || c.Enrich != nil || MonitorWorker(p) != Simple {
				t.Fatal("original request/enrichment decision differs")
			}
			if c.Provider == "smartrecruiters" && p.Profile != "smartrecruiters.api-urls/v1" {
				t.Fatal("original URL-only inventory changed")
			}
		})
	}
}
