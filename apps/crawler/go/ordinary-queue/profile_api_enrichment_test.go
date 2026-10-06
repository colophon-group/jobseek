package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestConfiguredAPIMonitorEnrichmentRequiresExplicitDelegation(t *testing.T) {
	base := profileConfig()
	base["crawler_type"] = "api_sniffer"
	cases := []struct {
		name, metadata string
		want           []string
		refused        bool
	}{
		{"auto-rich-skip", `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title"}}`, nil, false},
		{"explicit-skip", `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title"},"scraper_type":"skip"}`, nil, false},
		{"description", `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title"},"scraper_type":"embedded","scraper_config":{"enrich":["description"]}}`, []string{"description"}, false},
		{"structured", `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title"},"scraper_type":"api_sniffer","scraper_config":{"enrich":["description","locations","employment_type","date_posted","base_salary"]}}`, []string{"description", "locations", "employment_type", "date_posted", "base_salary"}, false},
		{"skip-conflict", `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title"},"scraper_type":"skip","scraper_config":{"enrich":["description"]}}`, nil, true},
		{"duplicate", `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title"},"scraper_config":{"enrich":["description","description"]}}`, nil, true},
		{"unknown", `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title"},"scraper_config":{"enrich":["permission"]}}`, nil, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			cfg := cloneConfig(base)
			cfg["metadata"] = c.metadata
			p, e := InspectRichMonitor(profileBoardID, cfg)
			if (e != nil) != c.refused {
				t.Fatal("delegation admission differs", e)
			}
			if e != nil {
				return
			}
			if p.Profile != "api_sniffer.http-items/v1" {
				t.Fatal("changed existing profile")
			}
			got, e := apiSnifferMonitorEnrichment(cfg)
			if e != nil || !reflect.DeepEqual(got, c.want) {
				t.Fatal("unconfigured detail work or lost delegated fields", got, e)
			}
		})
	}
}
func TestConfiguredAPIRegistryEnrichmentAssignments(t *testing.T) {
	file, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer file.Close()
	rows, e := csv.NewReader(file).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	head := map[string]int{}
	for i, k := range rows[0] {
		head[k] = i
	}
	admitted := 0
	for _, row := range rows[1:] {
		if row[head["monitor_type"]] != "api_sniffer" {
			continue
		}
		md := map[string]any{}
		if row[head["monitor_config"]] != "" && json.Unmarshal([]byte(row[head["monitor_config"]]), &md) != nil {
			t.Fatal("invalid monitor config")
		}
		sc := map[string]any{}
		if row[head["scraper_config"]] == "" || json.Unmarshal([]byte(row[head["scraper_config"]]), &sc) != nil {
			continue
		}
		fields, ok := sc["enrich"].([]any)
		if !ok || len(fields) == 0 {
			continue
		}
		md["scraper_type"], md["scraper_config"] = row[head["scraper_type"]], sc
		raw, e := json.Marshal(md)
		if e != nil {
			t.Fatal(e)
		}
		cfg := profileConfig()
		cfg["crawler_type"], cfg["board_url"], cfg["metadata"] = "api_sniffer", row[head["board_url"]], string(raw)
		if _, e := InspectRichMonitor(profileBoardID, cfg); e != nil {
			continue
		}
		got, e := apiSnifferMonitorEnrichment(cfg)
		if e != nil || len(got) != len(fields) {
			t.Fatal("accepted assignment lost enrichment")
		}
		admitted++
	}
	if admitted == 0 {
		t.Fatal("configured API enrichment coverage empty")
	}
	t.Logf("local configuration eligibility only: %d explicit API enrichment assignments", admitted)
}
