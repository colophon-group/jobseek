package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestJobStreetLegacySessionRegistryBindings(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	h := map[string]int{}
	for i, k := range rows[0] {
		h[k] = i
	}
	count := map[string]int{}
	for _, row := range rows[1:] {
		md := map[string]any{}
		_ = json.Unmarshal([]byte(row[h["monitor_config"]]), &md)
		kind := row[h["monitor_type"]]
		if kind != "jobstreet" && !(kind == "rss" && md["variant"] == "legacy") {
			continue
		}
		md["scraper_type"] = row[h["scraper_type"]]
		sc := map[string]any{}
		_ = json.Unmarshal([]byte(row[h["scraper_config"]]), &sc)
		md["scraper_config"] = sc
		body, _ := json.Marshal(md)
		c := profileConfig()
		c["crawler_type"], c["board_url"], c["metadata"] = kind, row[h["board_url"]], string(body)
		p, e := InspectRichMonitor(profileBoardID, c)
		if e != nil {
			t.Fatal(row[h["board_slug"]], e)
		}
		count[p.Profile]++
		if kind == "jobstreet" {
			if p.Profile != jobStreetMonitorProfile {
				t.Fatal(p)
			}
			d, e := inspectDetailOwnership(profileBoardID, c)
			if e != nil || d.Profile != jobStreetDetailProfile || d.Domain != "*" {
				t.Fatal(row[h["board_slug"]], d, e)
			}
			source := strings.TrimSuffix(d.Endpoint, "graphql") + "job/123"
			actual, e := inspectDetail(profileBoardID, c, source, Simple)
			if e != nil || actual.EffectiveBoardSHA256 != d.EffectiveBoardSHA256 || len(actual.EnrichmentFields) != 6 {
				t.Fatal(actual, e)
			}
			foreign := strings.Replace(source, "sg.jobstreet.com", "my.jobstreet.com", 1)
			if foreign == source {
				foreign = strings.Replace(source, "my.jobstreet.com", "sg.jobstreet.com", 1)
			}
			if _, e := inspectDetail(profileBoardID, c, foreign, Simple); e == nil {
				t.Fatal("foreign market accepted")
			}
			if _, e := inspectDetail(profileBoardID, c, source, Browser); e == nil {
				t.Fatal("browser details accepted")
			}
		} else {
			if p.Profile != legacySFSessionProfile || !LegacySFSessionResourceMatches(p, c, p.Endpoint) {
				t.Fatal(p)
			}
			origin := strings.Split(p.Endpoint, "/career?")[0]
			dwr := origin + "/xi/ajax/remoting/call/plaincall/careerJobSearchControllerProxy.search.dwr"
			if !RSSMonitorResourceMatches(p, c, dwr) || !initialMonitorResourceMatches(p, dwr) || RSSMonitorResourceMatches(p, c, dwr+"?extra=1") {
				t.Fatal("DWR resource binding")
			}
			c["monitor_needs_browser"] = "1"
			if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
				t.Fatal("unqualified browser session accepted")
			}
		}
	}
	if count[jobStreetMonitorProfile] != 3 || count[legacySFSessionProfile] != 3 {
		t.Fatal("registry coverage", count)
	}
}
