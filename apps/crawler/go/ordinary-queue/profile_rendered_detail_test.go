package queue

import (
	"encoding/json"
	"errors"
	"strings"
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

func TestRenderedFragmentsKeepBrowserAuthorityAndExactSource(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld", "nextdata"} {
		t.Run(scraper, func(t *testing.T) {
			c := renderedDetailConfig(scraper)
			if scraper == "nextdata" {
				c["metadata"] = `{"scraper_type":"nextdata","scraper_config":{"render":true,"path":"job","fields":{"title":"title","description":"description"}}}`
			}
			first, err := InspectRenderedDetail(jsonldBoardID, c, "https://jobs.example.net/job#first", Browser)
			if err != nil || first.SourceURL != "https://jobs.example.net/job#first" || first.Domain != "jobs.example.net" || detailWorker(first.Profile) != Browser {
				t.Fatal("fragment source lost browser authority", err)
			}
			second, err := InspectRenderedDetail(jsonldBoardID, c, "https://jobs.example.net/job#second", Browser)
			if err != nil || second.SourceURL == first.SourceURL || second.Profile != first.Profile || second.EffectiveBoardSHA256 != first.EffectiveBoardSHA256 {
				t.Fatal("fragment was stripped or changed canonical config identity", err)
			}
			for _, unsafe := range []string{"https://user:secret@jobs.example.net/job#first", "https://jobs.example.net:444/job#first", "file:///job#first"} {
				if _, err := InspectRenderedDetail(jsonldBoardID, c, unsafe, Browser); err == nil {
					t.Fatal("fragment weakened source URL guards")
				}
			}
			if _, err := InspectRenderedDetail(jsonldBoardID, c, first.SourceURL, Simple); err == nil {
				t.Fatal("fragment downgraded browser lane")
			}
		})
	}
}

func TestRenderedDetailActionPipelinesBindAllParserFamilies(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld", "nextdata"} {
		t.Run(scraper, func(t *testing.T) {
			c := renderedDetailConfig(scraper)
			var md map[string]any
			json.Unmarshal([]byte(c["metadata"]), &md)
			options := md["scraper_config"].(map[string]any)
			if scraper == "nextdata" {
				delete(options, "steps")
				options["path"] = "job"
				options["fields"] = map[string]any{"description": "body"}
			}
			options["actions"] = []any{map[string]any{"action": "wait", "ms": 0}, map[string]any{"action": "click", "selector": "#description", "required": true}}
			options["enrich"] = []string{"description"}
			raw, _ := json.Marshal(md)
			c["metadata"] = string(raw)
			p, err := InspectRenderedDetail(jsonldBoardID, c, "https://jobs.example.net/42", Browser)
			if err != nil || len(p.EnrichmentFields) != 1 || p.EnrichmentFields[0] != "description" {
				t.Fatal("valid action pipeline lost selection", p, err)
			}
			options["actions"].([]any)[1].(map[string]any)["selector"] = "#changed"
			raw, _ = json.Marshal(md)
			c["metadata"] = string(raw)
			changed, err := InspectRenderedDetail(jsonldBoardID, c, p.SourceURL, Browser)
			if err != nil || changed.EffectiveBoardSHA256 == p.EffectiveBoardSHA256 {
				t.Fatal("action change retained binding", err)
			}
			for _, invalid := range []any{[]any{map[string]any{"action": "unknown"}}, []any{map[string]any{"action": "click"}}, "click", []any{map[string]any{"action": "wait", "ms": -1}}} {
				options["actions"] = invalid
				raw, _ = json.Marshal(md)
				c["metadata"] = string(raw)
				if _, err := InspectRenderedDetail(jsonldBoardID, c, p.SourceURL, Browser); err == nil {
					t.Fatal("invalid action pipeline admitted")
				}
			}
		})
	}
}

func TestRenderedDetailRetainsUnsupportedNavigationAndPipeline(t *testing.T) {
	for name, value := range map[string]any{"proxy": "true", "skip_ssl": true, "same_origin_redirects": true, "actions": []any{map[string]any{"type": "click"}}, "enrich": []any{"unexpected"}, "request_headers": map[string]any{"X-User": "private"}, "timeout": 0, "wait": "unexpected", "wait_fallback": false, "routing_revision": "bad/path", "browser_backend": "chromium", "user_agent": "custom", "fetch_url_transform": map[string]any{}, "unknown": true} {
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

func TestRenderedDetailSelectsFieldsAndBindsCanonicalConfiguration(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld", "nextdata"} {
		t.Run(scraper, func(t *testing.T) {
			c := renderedDetailConfig(scraper)
			var md map[string]any
			json.Unmarshal([]byte(c["metadata"]), &md)
			options := md["scraper_config"].(map[string]any)
			if scraper == "nextdata" {
				delete(options, "steps")
				options["path"] = "job"
				options["fields"] = map[string]any{"title": "title", "description": "description"}
			}
			options["enrich"] = []any{"description"}
			body, _ := json.Marshal(md)
			c["metadata"] = string(body)
			p, e := InspectRenderedDetail(jsonldBoardID, c, "https://jobs.example.net/42", Browser)
			if e != nil || len(p.EnrichmentFields) != 1 || p.EnrichmentFields[0] != "description" {
				t.Fatal("rendered field selection refused", e, p.EnrichmentFields)
			}
			owner, e := inspectDetailOwnership(jsonldBoardID, c)
			if e != nil || owner.EffectiveBoardSHA256 != p.EffectiveBoardSHA256 || owner.Domain != "*" {
				t.Fatal("selected ownership differs", e)
			}
			options["enrich"] = []any{"employment_type"}
			body, _ = json.Marshal(md)
			c["metadata"] = string(body)
			changed, e := InspectRenderedDetail(jsonldBoardID, c, p.SourceURL, Browser)
			if e != nil || changed.EffectiveBoardSHA256 == p.EffectiveBoardSHA256 || changed.EnrichmentFields[0] != "employment_type" {
				t.Fatal("field selection lost source binding", e)
			}
			for _, bad := range []any{[]any{"description", "description"}, []any{"unknown"}, true, "description", map[string]any{"description": true}} {
				options["enrich"] = bad
				body, _ = json.Marshal(md)
				c["metadata"] = string(body)
				if _, e := InspectRenderedDetail(jsonldBoardID, c, p.SourceURL, Browser); e == nil {
					t.Fatal("ambiguous field selection admitted", bad)
				}
			}
		})
	}
}

func TestRenderedDOMAlternateReadRetainsCanonicalBrowserBinding(t *testing.T) {
	c := renderedDetailConfig("dom")
	c["metadata"] = `{"scraper_type":"dom","scraper_config":{"render":true,"steps":[{"tag":"h1","field":"title"}],"fetch_url_transform":{"find":"/job/","replace":"/print/"}}}`
	source := "https://jobs.example.net/job/42"
	p, err := InspectRenderedDetail(jsonldBoardID, c, source, Browser)
	if err != nil || p.SourceURL != source || p.Domain != "jobs.example.net" || detailWorker(p.Profile) != Browser {
		t.Fatal("alternate read changed canonical browser ownership", err)
	}
	owned, err := inspectDetailOwnership(jsonldBoardID, c)
	if err != nil || owned.EffectiveBoardSHA256 != p.EffectiveBoardSHA256 {
		t.Fatal("posting-only rewrite lost canonical ownership template", err)
	}
	if _, err := InspectRenderedDetail(jsonldBoardID, c, c["board_url"], Browser); err == nil {
		t.Fatal("template authority bypassed concrete route matching")
	}
	changed := cloneConfig(c)
	changed["metadata"] = strings.Replace(c["metadata"], "/print/", "/alternate/", 1)
	next, err := InspectRenderedDetail(jsonldBoardID, changed, source, Browser)
	if err != nil || next.EffectiveBoardSHA256 == p.EffectiveBoardSHA256 {
		t.Fatal("alternate target escaped full config binding", err)
	}
	if _, err := InspectRenderedDetail(jsonldBoardID, c, source, Simple); err == nil {
		t.Fatal("alternate read downgraded the browser lane")
	}
	for _, target := range []string{"file:///print/42", "https://user:pass@jobs.example.net/print/42"} {
		bad := cloneConfig(c)
		body, _ := json.Marshal(map[string]any{"scraper_type": "dom", "scraper_config": map[string]any{"render": true, "steps": []any{map[string]any{"tag": "h1", "field": "title"}}, "fetch_url_transform": map[string]any{"find": "^.*$", "replace": target}}})
		bad["metadata"] = string(body)
		if _, err := InspectRenderedDetail(jsonldBoardID, bad, source, Browser); err == nil {
			t.Fatal("unsafe alternate read admitted")
		}
	}
}
