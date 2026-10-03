package queue

import (
	"encoding/json"
	"os"
	"testing"
)

func TestRichProviderProfilesRetainActualTokenAndRegionSelection(t *testing.T) {
	for _, tc := range []struct{ kind, url, metadata, token, endpoint string }{
		{"ashby", "https://jobs.ashbyhq.com/inferred", `{"scraper_type":"skip"}`, "inferred", "https://api.ashbyhq.com/posting-api/job-board/inferred?includeCompensation=true"},
		{"ashby", "https://jobs.ashbyhq.com/inferred", `{"board_token":"ignored","org":"ignored","scraper_type":"skip"}`, "inferred", "https://api.ashbyhq.com/posting-api/job-board/inferred?includeCompensation=true"},
		{"ashby", "https://example.com/careers", `{"token":"explicit","blast_radius_floor":0.9,"scraper_type":"skip"}`, "explicit", "https://api.ashbyhq.com/posting-api/job-board/explicit?includeCompensation=true"},
		{"lever", "https://jobs.eu.lever.co/inferred", `{"scraper_type":"skip"}`, "inferred", "https://api.eu.lever.co/v0/postings/inferred?limit=100&skip=0"},
		{"lever", "https://jobs.lever.co/inferred", `{"company":"ignored","scraper_type":"skip"}`, "inferred", "https://api.lever.co/v0/postings/inferred?limit=100&skip=0"},
		{"lever", "https://jobs.eu.lever.co/inferred", `{"token":"explicit","region":"us","scraper_type":"skip"}`, "explicit", "https://api.lever.co/v0/postings/explicit?limit=100&skip=0"},
	} {
		t.Run(tc.kind+tc.metadata, func(t *testing.T) {
			config := profileConfig()
			config["crawler_type"] = tc.kind
			config["board_url"] = tc.url
			config["metadata"] = tc.metadata
			config["domain"] = tc.kind
			config["throttle_key"] = tc.kind
			profile, err := InspectRichMonitor(profileBoardID, config)
			if err != nil || profile.Token != tc.token || profile.Endpoint != tc.endpoint || profile.Profile != tc.kind+".token-skip/v1" || profile.SnapshotSHA256 != configDigest(config) {
				t.Fatalf("provider token/region binding changed: %+v error=%v", profile, err)
			}
			if _, err := InspectGreenhouseMonitor(profileBoardID, config); err == nil {
				t.Fatal("Greenhouse-only inspector accepted another provider")
			}
			config["scraper_needs_browser"] = "1"
			if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
				t.Fatal("rich skip profile admitted unwired browser detail")
			}
		})
	}
}

func TestRichProviderActualPythonRequestOracle(t *testing.T) {
	body, err := os.ReadFile("testdata/python_rich_profile_tokens.json")
	if err != nil {
		t.Fatal(err)
	}
	var oracle struct {
		Cases []struct {
			Kind, Token, Endpoint string
			BoardURL              string `json:"board_url"`
			Metadata              map[string]any
		}
	}
	if json.Unmarshal(body, &oracle) != nil || len(oracle.Cases) != 16 {
		t.Fatal("actual Python request oracle unavailable")
	}
	for _, row := range oracle.Cases {
		config := profileConfig()
		md, _ := json.Marshal(row.Metadata)
		config["crawler_type"], config["board_url"], config["metadata"] = row.Kind, row.BoardURL, string(md)
		profile, err := InspectRichMonitor(profileBoardID, config)
		if err != nil || profile.Token != row.Token || profile.Endpoint != row.Endpoint {
			t.Fatalf("Python provider request differs: %+v error=%v expected=%s", profile, err, row.Endpoint)
		}
	}
	for _, token := range []string{"../other", "fixture?x=1", "fixture#part", "fixture/other", "fixture\nheader"} {
		config := profileConfig()
		config["crawler_type"] = "ashby"
		md, _ := json.Marshal(map[string]string{"token": token, "scraper_type": "skip"})
		config["metadata"] = string(md)
		if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
			t.Fatal("non-component provider token admitted")
		}
	}
}

func TestRichProviderProfilesRefuseUnimplementedConfiguration(t *testing.T) {
	for _, md := range []string{`{"token":false,"scraper_type":"skip"}`, `{"token":"fixture","scraper_type":"json-ld"}`, `{"token":"fixture","scraper_type":"skip","blast_radius_floor":2}`, `{"token":"fixture","scraper_type":"skip","org":"legacy","filter":"unimplemented"}`} {
		config := profileConfig()
		config["crawler_type"] = "ashby"
		config["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
			t.Fatal("unimplemented profile admitted", md)
		}
	}
}
