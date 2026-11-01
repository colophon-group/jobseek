package queue

import (
	"encoding/json"
	"os"
	"testing"
)

func TestLeverExactBlankPostingExclusionAdmission(t *testing.T) {
	const id = "e0b53a9a-da36-4b09-a336-afb73d9ccffb"
	c := profileConfig()
	c["crawler_type"], c["board_url"] = "lever", "https://jobs.lever.co/rivieradininggroup"
	c["metadata"] = `{"token":"rivieradininggroup","scraper_type":"skip","exclude_blank_posting_ids":["` + id + `"]}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.LeverExcludeBlankPostingIDs != id || p.Profile != "lever.token-skip/v1" {
		t.Fatalf("exact Lever exclusion not bound to native profile: %+v err=%v", p, err)
	}
	for _, bad := range []string{
		`null`, `"bad"`, `["bad"]`, `[1]`, `["` + id + `","` + id + `"]`,
	} {
		c["metadata"] = `{"token":"rivieradininggroup","scraper_type":"skip","exclude_blank_posting_ids":` + bad + `}`
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatalf("invalid exclusion admitted: %s", bad)
		}
	}
}

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
	for _, md := range []string{`{"token":false,"scraper_type":"skip"}`, `{"token":"fixture","scraper_type":"json-ld","scraper_config":{"enrich":["description"]}}`, `{"token":"fixture","scraper_type":"skip","blast_radius_floor":2}`, `{"token":"fixture","scraper_type":"skip","org":"legacy","filter":"unimplemented"}`} {
		config := profileConfig()
		config["crawler_type"] = "ashby"
		config["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, config); err == nil {
			t.Fatal("unimplemented profile admitted", md)
		}
	}
}

func TestRichProvidersRetainInactiveDetailConfigWithoutSchedulingEnrichment(t *testing.T) {
	for _, c := range []struct{ provider, board, metadata, profile string }{
		{"ashby", "https://nordsecurity.com/careers", `{"token":"nord-security","scraper_type":"json-ld","scraper_config":{"wait":"networkidle","render":true,"timeout":30000,"wait_fallback":"domcontentloaded"}}`, "ashby.token-items/v1"},
		{"lever", "https://jobs.eu.lever.co/volta-medical", `{"token":"volta-medical","region":"eu","scraper_type":"json-ld"}`, "lever.token-items/v1"},
		{"recruitee", "https://jobs.floryn.com/", `{"api_base":"https://jobs.floryn.com","scraper_type":"json-ld","scraper_config":{"render":false}}`, "recruitee.api-items/v1"},
	} {
		t.Run(c.provider, func(t *testing.T) {
			config := profileConfig()
			config["crawler_type"], config["board_url"], config["metadata"] = c.provider, c.board, c.metadata
			config["scraper_needs_browser"] = "1"
			p, e := InspectRichMonitor(profileBoardID, config)
			if e != nil || p.Profile != c.profile || p.SnapshotSHA256 != configDigest(config) || config["metadata"] != c.metadata {
				t.Fatal("retained detail config lost", e)
			}
			var md map[string]any
			if json.Unmarshal([]byte(c.metadata), &md) != nil {
				t.Fatal("metadata")
			}
			// PostgreSQL jsonb and Redis may arrange retained nested options differently.
			body, _ := json.Marshal(md)
			reordered := cloneConfig(config)
			reordered["metadata"] = string(body)
			same, e := InspectRichMonitor(profileBoardID, reordered)
			if e != nil || same.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 {
				t.Fatal("semantic binding changed", e)
			}
			for _, bad := range []any{map[string]any{"enrich": []string{"description"}}, map[string]any{"enrich": true}, []string{"invalid"}} {
				md["scraper_config"] = bad
				body, _ := json.Marshal(md)
				reordered["metadata"] = string(body)
				if _, e := InspectRichMonitor(profileBoardID, reordered); e == nil {
					t.Fatal("unimplemented enrichment admitted")
				}
			}
		})
	}
}

func TestLeverSelectedEnrichmentRetainsSealedAPIAndFullBinding(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["board_url"] = "lever", "https://jobs.lever.co/fixture"
	c["metadata"] = `{"token":"fixture","scraper_type":"api_sniffer","scraper_config":{"fields":{"description":"description","date_posted":"createdAt"},"enrich":["description","date_posted"]}}`
	p, err := InspectRichMonitor(profileBoardID, c)
	fields, e := leverMonitorEnrichment(c)
	if err != nil || e != nil || len(fields) != 2 || p.Profile != "lever.token-items/v1" || p.Endpoint != "https://api.lever.co/v0/postings/fixture?limit=100&skip=0" {
		t.Fatal("selected detail delegation changed list authority", err, e)
	}
	var md map[string]any
	json.Unmarshal([]byte(c["metadata"]), &md)
	b, _ := json.Marshal(md)
	c["metadata"] = string(b)
	reordered, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || reordered.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 {
		t.Fatal("nested SQL/cache key order changed binding", err)
	}
	for _, bad := range []string{`["title"]`, `["description","description"]`, `true`, `[]`} {
		c["metadata"] = `{"token":"fixture","scraper_type":"api_sniffer","scraper_config":{"enrich":` + bad + `}}`
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unqualified Lever enrichment admitted")
		}
	}
}
