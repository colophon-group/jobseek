package queue

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func domDetailConfig() map[string]string {
	c := profileConfig()
	c["crawler_type"], c["board_url"] = "dom", "https://careers.example.com/jobs"
	c["domain"], c["throttle_key"] = "careers.example.com", "careers.example.com"
	c["monitor_needs_browser"] = "1"
	c["metadata"] = `{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}],"defaults":{"language":"en"}},"selector":"a.job","render":true}`
	return c
}
func TestDOMDetailOwnsCanonicalPostingAcrossHostsIndependentlyOfMonitor(t *testing.T) {
	c := domDetailConfig()
	profile, err := InspectDOMDetail(jsonldBoardID, c, "https://jobs.example.net/job/42", Simple)
	if err != nil || profile.Domain != "jobs.example.net" || profile.Profile != domDetailProfile || profile.Endpoint != profile.SourceURL {
		t.Fatal("direct detail did not admit actual source", err)
	}
	doc := testOwnershipDocument(t)
	metadata, err := profileMetadataFields(c["metadata"], nil)
	if err != nil {
		t.Fatal(err)
	}
	stable, err := stableJSONLDConfig(c, metadata)
	if err != nil {
		t.Fatal(err)
	}
	doc.Details = []ownershipDetail{{BoardID: jsonldBoardID, Domain: "*", Profile: domDetailProfile, Worker: Simple, CompanyID: profile.CompanyID, EffectiveConfigHash: profile.EffectiveBoardSHA256, Config: stable}}
	body, hash := testOwnershipBody(t, doc)
	plan, err := decodeOwnership(body, hash)
	if err != nil {
		t.Fatal(err)
	}
	var projection ownershipProjectionDocument
	if json.Unmarshal([]byte(plan.ProjectionJSON()), &projection) != nil || projection.Members[jsonldBoardID] != "" || projection.Details[jsonldBoardID] != "*" {
		t.Fatal("detail adoption changed monitor owner")
	}
	c["metadata"] = `{"scraper_type":"dom","selector":"changed","scraper_config":{"steps":[{"tag":"h1","field":"title"}]}}`
	changed, err := InspectDOMDetail(jsonldBoardID, c, profile.SourceURL, Simple)
	if err != nil || changed.EffectiveBoardSHA256 == profile.EffectiveBoardSHA256 {
		t.Fatal("changed board config retained identity")
	}
}
func TestDOMDetailRefusesUnimplementedTransportAndPipeline(t *testing.T) {
	for name, change := range map[string]func(map[string]string){
		"browser": func(c map[string]string) { c["scraper_needs_browser"] = "1" },
		"proxy": func(c map[string]string) {
			c["metadata"] = `{"scraper_type":"dom","scraper_config":{"proxy":"enabled"}}`
		},
		"render": func(c map[string]string) {
			c["metadata"] = `{"scraper_type":"dom","scraper_config":{"render":true}}`
		},
		"tls": func(c map[string]string) {
			c["metadata"] = `{"scraper_type":"dom","scraper_config":{"skip_ssl":true}}`
		},
		"fallback": func(c map[string]string) {
			c["metadata"] = `{"scraper_type":"dom","scraper_config":{"fallback":["dom"]}}`
		},
		"enrich": func(c map[string]string) {
			c["metadata"] = `{"scraper_type":"dom","scraper_config":{"enrich":["title"]}}`
		},
		"duplicates": func(c map[string]string) {
			c["metadata"] = `{"scraper_type":"dom","scraper_config":{"proxy":true,"proxy":false}}`
		},
		"metadata_duplicates": func(c map[string]string) { c["metadata"] = `{"scraper_type":"dom","scraper_type":"workday"}` },
		"foreign_field":       func(c map[string]string) { c["runtime_url"] = "https://foreign.example" },
		"other_type":          func(c map[string]string) { c["metadata"] = `{"scraper_type":"dom"}` },
	} {
		t.Run(name, func(t *testing.T) {
			c := domDetailConfig()
			change(c)
			if _, err := InspectDOMDetail(jsonldBoardID, c, "https://jobs.example.net/job/42", Simple); !errors.Is(err, ErrUnsupportedProfile) {
				t.Fatal("unsafe detail admitted", err)
			}
		})
	}
	for _, source := range []string{"https://u:p@jobs.example.net/a", "file:///tmp/a", "https://jobs.example.net:444/a", "https://jobs.example.net/a\x00"} {
		if _, err := InspectDOMDetail(jsonldBoardID, domDetailConfig(), source, Simple); err == nil {
			t.Fatal("unsafe URL admitted")
		}
	}
}
func TestDOMDetailLifecycleCountersDoNotReplaceImmutableConfiguration(t *testing.T) {
	c := domDetailConfig()
	before, err := inspectDetailOwnership(jsonldBoardID, c)
	if err != nil {
		t.Fatal(err)
	}
	c["metadata"] = strings.TrimSuffix(c["metadata"], "}") + `,"suspect_streak":4,"_confirmed_drop_candidate":true}`
	after, err := inspectDetailOwnership(jsonldBoardID, c)
	if err != nil || before.EffectiveBoardSHA256 != after.EffectiveBoardSHA256 {
		t.Fatal("runtime observation changed owner", err)
	}
}

func TestDOMDetailCanonicalizesNestedSQLAndRedisMetadata(t *testing.T) {
	canonical, cached := domDetailConfig(), domDetailConfig()
	canonical["metadata"] = `{"scraper_type":"dom","scraper_config":{"defaults":{"title":"Engineer","language":"en"},"steps":[{"tag":"h1","field":"title"}]},"selector":"a.job","render":true}`
	cached["metadata"] = `{"render":true,"selector":"a.job","scraper_config":{"steps":[{"field":"title","tag":"h1"}],"defaults":{"language":"en","title":"Engineer"}},"scraper_type":"dom"}`
	a, err := InspectDOMDetail(jsonldBoardID, canonical, canonical["board_url"], Simple)
	if err != nil {
		t.Fatal(err)
	}
	b, err := InspectDOMDetail(jsonldBoardID, cached, cached["board_url"], Simple)
	if err != nil || a.EffectiveBoardSHA256 != b.EffectiveBoardSHA256 {
		t.Fatal("equivalent nested metadata changed ownership", err)
	}
}

func TestDOMDetailRetainsUnsupportedSecondaryAndTransportContracts(t *testing.T) {
	for _, extra := range []string{
		`"proxy":true`, `"render":true`, `"skip_ssl":true`, `"ssl_verify":false`,
		`"actions":[{"action":"click","selector":"button"}]`, `"enrich":["json-ld"]`,
		`"document_fallback":{"pdf":{}}`, `"linked_description":{"selector":"a.detail"}`,
		`"fetch_url_transform":{"find":"a","replace":"b"}`, `"encoding":"utf-16"`,
		`"request_headers":false`, `"request_headers":{"Authorization":"private"}`,
		`"request_headers":{"Accept":"text/html"," Accept ":"text/plain"}`,
		`"retry_statuses":{"429":6}`, `"unknown":true`,
	} {
		c := domDetailConfig()
		c["metadata"] = `{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}],` + extra + `}}`
		if _, err := InspectDOMDetail(jsonldBoardID, c, "https://example.com/job", Simple); !errors.Is(err, ErrUnsupportedProfile) {
			t.Fatal("unsupported DOM contract adopted", extra, err)
		}
	}
}
