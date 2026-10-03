package queue

import (
	"encoding/json"
	"os"
	"testing"
)

func TestTenantProfilesMatchActualPythonRequestsAndBoundConfiguration(t *testing.T) {
	body, err := os.ReadFile("testdata/python_tenant_profile_requests.json")
	if err != nil {
		t.Fatal(err)
	}
	var oracle struct {
		Cases []struct {
			Kind, Endpoint string
			BoardURL       string `json:"board_url"`
			Metadata       map[string]any
		}
	}
	if json.Unmarshal(body, &oracle) != nil || len(oracle.Cases) != 13 {
		t.Fatal("actual Python request oracle missing")
	}
	for _, row := range oracle.Cases {
		config := profileConfig()
		md, _ := json.Marshal(row.Metadata)
		config["crawler_type"], config["board_url"], config["metadata"] = row.Kind, row.BoardURL, string(md)
		config["domain"], config["throttle_key"] = row.Kind, row.Kind
		profile, err := InspectRichMonitor(profileBoardID, config)
		if err != nil || profile.Endpoint != row.Endpoint {
			t.Fatalf("Python tenant request changed: kind=%s endpoint=%s expected=%s error=%v", row.Kind, profile.Endpoint, row.Endpoint, err)
		}
		config["metadata"] = `{"scraper_type":"skip","slug":"different","api_base":"https://different.example.com"}`
		if row.Kind == "pinpoint" {
			config["metadata"] = `{"scraper_type":"skip","slug":"different"}`
		}
		changed, err := InspectRichMonitor(profileBoardID, config)
		if err != nil || changed.EffectiveConfigSHA256 == profile.EffectiveConfigSHA256 {
			t.Fatal("changed endpoint did not revoke profile binding")
		}
	}
}

func TestTenantProfilesRejectUnwiredDetailTransportAndConfiguration(t *testing.T) {
	for _, tc := range []struct{ kind, metadata string }{
		{"pinpoint", `{"scraper_type":"skip","slug":"../other"}`},
		{"pinpoint", `{"scraper_type":"skip","slug":"fixture","api_base":"https://example.com"}`},
		{"recruitee", `{"scraper_type":"skip","api_base":"https://secret@api.example.com"}`},
		{"recruitee", `{"scraper_type":"skip","api_base":"https://example.com?x=1"}`},
		{"recruitee", `{"scraper_type":"skip","api_base":"http://example.com"}`},
		{"recruitee", `{"scraper_type":"json-ld","slug":"fixture"}`},
		{"recruitee", `{"scraper_type":"skip","slug":"fixture","scraper_config":{"enrich":["description"]}}`},
	} {
		config := profileConfig()
		config["crawler_type"], config["metadata"] = tc.kind, tc.metadata
		if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
			t.Fatal("unwired tenant profile accepted", tc.kind)
		}
	}
}
