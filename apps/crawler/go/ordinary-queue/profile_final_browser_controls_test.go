package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestRSSRescrapeAnnotationRetainsFeedAndOwnership(t *testing.T) {
	c := profileConfig()
	c["crawler_type"] = "rss"
	c["metadata"] = `{"preset":"generic","feed_url":"https://example.com/feed","scraper_type":"skip","rescrape_policy":"never"}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "rss.generic-skip/v1" {
		t.Fatal("shared scheduling annotation rejected", err)
	}
	other := cloneConfig(c)
	other["metadata"] = strings.Replace(c["metadata"], `,"rescrape_policy":"never"`, "", 1)
	without, err := InspectRichMonitor(profileBoardID, other)
	if err != nil || without.Endpoint != p.Endpoint || without.Profile != p.Profile || without.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("annotation changed extraction or lost immutable binding", err)
	}
	for _, invalid := range []string{`null`, `true`, `"always"`, `[]`} {
		other["metadata"] = strings.Replace(c["metadata"], `"never"`, invalid, 1)
		if _, err := InspectRichMonitor(profileBoardID, other); err == nil {
			t.Fatal("unsupported scheduling annotation admitted")
		}
	}
}

func TestRSSDelegatesLocationTypeWithoutDelegatingListingTitle(t *testing.T) {
	for _, render := range []bool{false, true} {
		c := profileConfig()
		c["crawler_type"] = "rss"
		c["metadata"] = `{"preset":"wp_job_manager","feed_url":"https://example.com/feed","scraper_type":"json-ld","scraper_config":{"enrich":["description","locations","employment_type","job_location_type"]}}`
		if render {
			c["monitor_needs_browser"] = "1"
			c["metadata"] = strings.Replace(c["metadata"], `{"preset"`, `{"render":true,"preset"`, 1)
		}
		p, err := InspectRichMonitor(profileBoardID, c)
		fields, fieldErr := RSSMonitorEnrichment(c)
		if err != nil || fieldErr != nil || (MonitorWorker(p) == Browser) != render || !reflect.DeepEqual(fields, []string{"description", "locations", "employment_type", "job_location_type"}) {
			t.Fatal("RSS selected detail assignment differs", err, fieldErr)
		}
		for _, invalid := range []string{`"title"`, `"job_location_type","job_location_type"`, `"unknown"`} {
			other := cloneConfig(c)
			other["metadata"] = strings.Replace(c["metadata"], `"job_location_type"`, invalid, 1)
			if _, err := InspectRichMonitor(profileBoardID, other); err == nil {
				t.Fatal("invalid or listing-owned field delegated")
			}
		}
	}
}

func TestRenderedDOMFragmentRetainsNavigationAndExactAuthority(t *testing.T) {
	for _, slug := range []string{"ltimindtree-careers-india-europe", "ltimindtree-careers-us-canada"} {
		c := finalBrowserRegistryConfig(t, slug)
		p, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || p.Endpoint != c["board_url"] || MonitorWorker(p) != Browser || !DOMMonitorResourceMatches(p, c, p.Endpoint) {
			t.Fatal("SPA fragment or browser actions lost", err)
		}
		changed := cloneConfig(c)
		changed["board_url"] += "-unqualified"
		if _, err := InspectRichMonitor(profileBoardID, changed); err == nil || DOMMonitorResourceMatches(p, c, changed["board_url"]) {
			t.Fatal("unqualified fragment route acquired ownership")
		}
		changed = cloneConfig(c)
		changed["monitor_needs_browser"] = "0"
		changed["metadata"] = `{"render":false,"scraper_type":"json-ld"}`
		if _, err := InspectRichMonitor(profileBoardID, changed); err == nil {
			t.Fatal("HTTP route accepted a browser fragment")
		}
	}
}

func finalBrowserRegistryConfig(t *testing.T, slug string) map[string]string {
	t.Helper()
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
	for i, key := range rows[0] {
		h[key] = i
	}
	for _, row := range rows[1:] {
		if row[h["board_slug"]] != slug {
			continue
		}
		var md map[string]any
		var sc any
		if json.Unmarshal([]byte(row[h["monitor_config"]]), &md) != nil || json.Unmarshal([]byte(row[h["scraper_config"]]), &sc) != nil {
			t.Fatal("registry metadata malformed")
		}
		md["scraper_type"], md["scraper_config"] = row[h["scraper_type"]], sc
		body, _ := json.Marshal(md)
		c := domMonitorConfig()
		c["board_slug"], c["board_url"], c["metadata"] = slug, row[h["board_url"]], string(body)
		c["monitor_needs_browser"], c["scraper_needs_browser"] = "1", "1"
		return c
	}
	t.Fatal("qualified registry source missing")
	return nil
}

func TestFinalDetailTransitionsPreserveExistingMonitorContracts(t *testing.T) {
	for _, entry := range finalDetailTransitions {
		c := finalBrowserRegistryConfig(t, entry.Slug)
		md, err := richProfileMetadata(c)
		if err != nil || !qualifiedFinalDetailTransition(c, md) {
			t.Fatal("exact detail transition rejected", err)
		}
		current, err := InspectRichMonitor(profileBoardID, c)
		if err != nil {
			t.Fatal("retained monitor rejected", err)
		}
		listing, navigation, err := RenderedDOMMonitorOptions(c)
		if err != nil || !listing.RequireNonEmpty || current.BrowserProxy {
			t.Fatal("retained monitor lost its qualified direct read", err)
		}
		md["scraper_config"] = entry.Reverse
		body, _ := json.Marshal(md)
		legacy := cloneConfig(c)
		legacy["metadata"] = string(body)
		previous, err := InspectRichMonitor(profileBoardID, legacy)
		oldListing, oldNavigation, optionsErr := RenderedDOMMonitorOptions(legacy)
		if err != nil || optionsErr != nil || current.EffectiveConfigSHA256 == previous.EffectiveConfigSHA256 || current.Profile != previous.Profile || !reflect.DeepEqual(listing, oldListing) || !reflect.DeepEqual(navigation, oldNavigation) {
			t.Fatal("unchanged monitor extraction or new ownership binding differs", err, optionsErr)
		}
		for key, raw := range map[string]json.RawMessage{"scraper_config": json.RawMessage(`{"render":true}`), "url_filter": json.RawMessage(`"/unqualified/"`)} {
			changed := cloneConfig(c)
			var changedMD map[string]json.RawMessage
			json.Unmarshal([]byte(c["metadata"]), &changedMD)
			changedMD[key] = raw
			body, _ := json.Marshal(changedMD)
			changed["metadata"] = string(body)
			if qualifiedFinalDetailTransition(changed, changedMD) {
				t.Fatal("unproved contract inherited monitor permission")
			}
		}
	}
}
