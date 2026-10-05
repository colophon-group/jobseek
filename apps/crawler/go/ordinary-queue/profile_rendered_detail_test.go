package queue

import (
	"encoding/json"
	"errors"
	"testing"
)

func renderedDetailConfig(scraper string) map[string]string {
	c := domDetailConfig()
	c["scraper_needs_browser"] = "1"
	parser := map[string]any{"render": true, "steps": []any{map[string]any{"tag": "h1", "field": "title"}}}
	if scraper == "json-ld" {
		delete(parser, "steps")
	}
	body, _ := json.Marshal(map[string]any{"scraper_type": scraper, "scraper_config": parser})
	c["metadata"] = string(body)
	return c
}

func TestRenderedDetailBindsOriginalConfigurationAndBrowserWorker(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld"} {
		t.Run(scraper, func(t *testing.T) {
			c := renderedDetailConfig(scraper)
			profile, err := InspectRenderedDetail(jsonldBoardID, c, "https://jobs.example.net/42", Browser)
			if err != nil || detailWorker(profile.Profile) != Browser || profile.Domain != "jobs.example.net" {
				t.Fatal("rendered admission failed", err)
			}
			if _, err := InspectRenderedDetail(jsonldBoardID, c, profile.SourceURL, Simple); !errors.Is(err, ErrUnsupportedProfile) {
				t.Fatal("simple queue admitted browser profile")
			}
			owned, err := inspectDetailOwnership(jsonldBoardID, c)
			if err != nil || owned.Domain != "*" || owned.EffectiveBoardSHA256 != profile.EffectiveBoardSHA256 {
				t.Fatal("canonical ownership differs", err)
			}
			doc := testOwnershipDocument(t)
			metadata, _ := profileMetadataFields(c["metadata"], nil)
			stable, err := stableJSONLDConfig(c, metadata)
			if err != nil {
				t.Fatal(err)
			}
			doc.Details = []ownershipDetail{{BoardID: jsonldBoardID, Domain: "*", Profile: profile.Profile, Worker: Browser, CompanyID: profile.CompanyID, EffectiveConfigHash: profile.EffectiveBoardSHA256, Config: stable}}
			body, hash := testOwnershipBody(t, doc)
			if _, err := decodeOwnership(body, hash); err != nil {
				t.Fatal("browser ownership document rejected", err)
			}
			doc.Details[0].Worker = Simple
			body, hash = testOwnershipBody(t, doc)
			if _, err := decodeOwnership(body, hash); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("wrong worker retained ownership")
			}
			c["metadata"] = c["metadata"][:len(c["metadata"])-1] + `,"selector":"changed"}`
			changed, err := InspectRenderedDetail(jsonldBoardID, c, profile.SourceURL, Browser)
			if err != nil || changed.EffectiveBoardSHA256 == profile.EffectiveBoardSHA256 {
				t.Fatal("full canonical board change retained binding", err)
			}
		})
	}
}

func TestRenderedDetailRetainsUnsupportedNavigationAndPipeline(t *testing.T) {
	for name, value := range map[string]any{"proxy": true, "skip_ssl": true, "same_origin_redirects": true, "actions": []any{map[string]any{"type": "click"}}, "enrich": []any{"title"}, "request_headers": map[string]any{"X-User": "private"}, "timeout": 0, "wait": "unexpected", "wait_fallback": false, "routing_revision": "bad/path", "browser_backend": "chromium", "user_agent": "custom", "fetch_url_transform": map[string]any{}, "unknown": true} {
		t.Run(name, func(t *testing.T) {
			c := renderedDetailConfig("dom")
			var metadata map[string]any
			json.Unmarshal([]byte(c["metadata"]), &metadata)
			metadata["scraper_config"].(map[string]any)[name] = value
			body, _ := json.Marshal(metadata)
			c["metadata"] = string(body)
			if _, err := InspectRenderedDetail(jsonldBoardID, c, "https://jobs.example.net/42", Browser); !errors.Is(err, ErrUnsupportedProfile) {
				t.Fatal("unsupported profile admitted", err)
			}
		})
	}
	c := renderedDetailConfig("json-ld")
	if _, err := InspectRenderedDetail(jsonldBoardID, c, "https://careers.icims.com/jobs/42", Browser); !errors.Is(err, ErrUnsupportedProfile) {
		t.Fatal("unimplemented iframe recovery adopted")
	}
	c = renderedDetailConfig("dom")
	c["metadata"] = `{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}],"render":false,"render":true}}`
	if _, err := InspectRenderedDetail(jsonldBoardID, c, "https://jobs.example.net/42", Browser); !errors.Is(err, ErrUnsupportedProfile) {
		t.Fatal("duplicate browser option adopted")
	}
}
