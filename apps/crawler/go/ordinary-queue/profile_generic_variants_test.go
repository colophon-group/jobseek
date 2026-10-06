package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestGroupedGenericCurrentRegistryEligibility(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	head := map[string]int{}
	for n, key := range rows[0] {
		head[key] = n
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[head["monitor_type"]]
		if provider != "dom" && provider != "rss" && provider != "inline" {
			continue
		}
		md := map[string]any{}
		if raw := row[head["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("invalid registry JSON")
		}
		if raw := row[head["scraper_type"]]; raw != "" {
			md["scraper_type"] = raw
		}
		if raw := row[head["scraper_config"]]; raw != "" {
			var sc any
			if json.Unmarshal([]byte(raw), &sc) != nil {
				t.Fatal("invalid scraper JSON")
			}
			md["scraper_config"] = sc
		}
		raw, e := json.Marshal(md)
		if e != nil {
			t.Fatal(e)
		}
		cfg := profileConfig()
		cfg["crawler_type"], cfg["board_url"], cfg["metadata"] = provider, row[head["board_url"]], string(raw)
		if md["render"] == true {
			cfg["monitor_needs_browser"] = "1"
		}
		p, e := InspectRichMonitor(profileBoardID, cfg)
		if e != nil {
			continue
		}
		if provider == "dom" {
			o, e := DOMMonitorOptions(cfg)
			if e != nil {
				t.Fatal(e)
			}
			if o.Pagination != nil {
				counts["dom_pagination"]++
			}
			if md["url_transform"] != nil {
				counts["dom_transform"]++
			}
		}
		if provider == "rss" && (p.Profile == "rss.generic-skip/v1" || p.Profile == "rss.generic-items/v1") {
			counts["rss_generic"]++
		}
		if provider == "inline" {
			o, e := InlineMonitorOptions(cfg)
			if e != nil {
				t.Fatal(e)
			}
			for _, c := range o.Candidates {
				if c.Document.InlineCacheBypass {
					counts["inline_cache"]++
					break
				}
			}
		}
	}
	for _, key := range []string{"dom_pagination", "dom_transform", "rss_generic", "inline_cache"} {
		if counts[key] == 0 {
			t.Fatal("grouped registry coverage empty", key)
		}
	}
	t.Logf("local configuration eligibility only: %v", counts)
}

func TestDOMPaginationBindsOnlyConfiguredPagesAndKeepsRenderedPagesExcluded(t *testing.T) {
	cfg := domMonitorConfig()
	cfg["metadata"] = `{"url_filter":"/jobs/","pagination":{"param_name":"page","start":1,"increment":2,"max_pages":3},"url_transform":{"find":"\\?tracking=.*$","replace":""},"scraper_type":"json-ld"}`
	p, e := InspectRichMonitor(profileBoardID, cfg)
	if e != nil {
		t.Fatal(e)
	}
	for _, test := range []struct {
		url      string
		accepted bool
	}{{p.Endpoint, true}, {p.Endpoint + "&page=3", true}, {p.Endpoint + "&page=5", true}, {p.Endpoint + "&page=2", false}, {p.Endpoint + "&page=7", false}, {"https://foreign.example.com/listing?locale=en&page=3", false}, {p.Endpoint + "&page=3&unbound=x", false}} {
		if got := DOMMonitorResourceMatches(p, cfg, test.url); got != test.accepted {
			t.Fatal("page acquired wrong resource scope", test.url)
		}
	}
	cfg["monitor_needs_browser"] = "1"
	cfg["metadata"] = `{"render":true,"pagination":{"param_name":"page","max_pages":3},"scraper_type":"json-ld"}`
	if _, e = InspectRichMonitor(profileBoardID, cfg); e == nil {
		t.Fatal("single-page rendered engine acquired pagination")
	}
}
