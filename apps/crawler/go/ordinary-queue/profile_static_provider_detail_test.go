package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestStaticProviderDetailCanonicalRegistryBindings(t *testing.T) {
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	h := map[string]int{}
	for i, k := range rows[0] {
		h[k] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		scraper := row[h["scraper_type"]]
		want := map[string]string{"linkedin": linkedInDetailProfile, "jazzhr": jazzHRDetailProfile, "taleo": taleoEnterpriseDetailProfile}[scraper]
		if want == "" {
			continue
		}
		t.Run(row[h["board_slug"]], func(t *testing.T) {
			md := map[string]any{}
			if raw := row[h["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
				t.Fatal("monitor config")
			}
			md["scraper_type"] = scraper
			if raw := row[h["scraper_config"]]; raw != "" {
				var options any
				if json.Unmarshal([]byte(raw), &options) != nil {
					t.Fatal("scraper config")
				}
				md["scraper_config"] = options
			}
			body, _ := json.Marshal(md)
			c := profileConfig()
			c["crawler_type"], c["board_url"], c["metadata"] = row[h["monitor_type"]], row[h["board_url"]], string(body)
			p, err := inspectDetailOwnership(profileBoardID, c)
			if err != nil || p.Profile != want || p.Domain != "*" || !independentDetailProfile(p.Profile) {
				t.Fatal(p, err)
			}
			source := p.Endpoint
			if scraper == "linkedin" {
				source = "https://ch.linkedin.com/jobs/view/engineer-123?tracking=1"
			}
			actual, err := inspectDetail(profileBoardID, c, source, Simple)
			if err != nil || actual.Profile != want || actual.SourceURL != source || actual.EffectiveBoardSHA256 != p.EffectiveBoardSHA256 || !detailDomainMatches(p.Domain, actual) {
				t.Fatal(actual, err)
			}
			if scraper == "linkedin" && actual.Domain != "ch.linkedin.com" {
				t.Fatal("localized posting domain replaced by guest endpoint", actual.Domain)
			}
			if raw := row[h["scraper_config"]]; raw != "" && !reflect.DeepEqual(actual.EnrichmentFields, []string{"description", "employment_type", "job_location_type"}) {
				t.Fatal("selected fields changed", actual.EnrichmentFields)
			}
			if _, err := InspectStaticProviderDetail(profileBoardID, c, source, Browser); err == nil {
				t.Fatal("browser admitted")
			}
			if _, err := InspectStaticProviderDetail(profileBoardID, c, "https://foreign.example/jobs/123", Simple); err == nil {
				t.Fatal("foreign source admitted")
			}
			changed := cloneConfig(c)
			changed["company_id"] = "33333333-3333-3333-3333-333333333333"
			bound, err := inspectDetailOwnership(profileBoardID, changed)
			if err != nil || bound.EffectiveBoardSHA256 == p.EffectiveBoardSHA256 {
				t.Fatal("canonical hash not bound")
			}
			for _, key := range []string{"proxy", "render", "ssl_verify", "request_headers", "endpoint", "unknown", "fallback"} {
				md["scraper_config"] = map[string]any{key: true}
				bad, _ := json.Marshal(md)
				changed := cloneConfig(c)
				changed["metadata"] = string(bad)
				if _, err := inspectDetailOwnership(profileBoardID, changed); err == nil {
					t.Fatal("unproved option admitted", key)
				}
			}
		})
		counts[scraper]++
	}
	if !reflect.DeepEqual(counts, map[string]int{"linkedin": 34, "jazzhr": 20, "taleo": 5}) {
		t.Fatal("registry changed", counts)
	}
}
