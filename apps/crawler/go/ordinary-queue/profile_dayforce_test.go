package queue

import (
	"encoding/csv"
	"encoding/json"
	"errors"
	"os"
	"testing"
)

func dayforceProfileConfig() map[string]string {
	c := profileConfig()
	c["crawler_type"] = "dayforce"
	c["monitor_needs_browser"] = "1"
	c["board_url"] = "https://jobs.dayforcehcm.com/en-US/fixture/EXTERNAL"
	c["metadata"] = `{"tenant":"fixture","portal":"EXTERNAL","offset_overlap":5,"scraper_type":"skip"}`
	return c
}

func TestDayforceCompiledBrowserOwnershipAndResourceScope(t *testing.T) {
	c := dayforceProfileConfig()
	p, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || p.Profile != dayforceMonitorProfile || MonitorWorker(p) != Browser || p.Endpoint != "https://jobs.dayforcehcm.com/fixture/EXTERNAL" {
		t.Fatal("browser profile not bound", e)
	}
	md, _ := richProfileMetadata(c)
	stable, _ := stableJSONLDConfig(c, md)
	doc := testOwnershipDocument(t)
	doc.Members = []ownershipMember{{profileBoardID, p.CompanyID, p.Domain, Monitor, Browser, p.Profile, p.EffectiveConfigSHA256, stable}}
	body, hash := testOwnershipBody(t, doc)
	if _, e = decodeOwnership(body, hash); e != nil {
		t.Fatal("compiled browser owner rejected", e)
	}
	doc.Members[0].Worker = Simple
	body, hash = testOwnershipBody(t, doc)
	if _, e = decodeOwnership(body, hash); !errors.Is(e, ErrAuthorityLost) {
		t.Fatal("direct worker accepted browser ownership", e)
	}
	for _, url := range []string{p.Endpoint, "https://jobs.dayforcehcm.com/en-US/fixture/EXTERNAL", "https://jobs.dayforcehcm.com/api/geo/fixture/jobposting/search"} {
		if !SecondaryMonitorResourceMatches(p, c, url) {
			t.Fatal("same board resource rejected")
		}
	}
	for _, url := range []string{"https://jobs.dayforcehcm.com/foreign/EXTERNAL", "https://jobs.dayforcehcm.com/api/geo/foreign/jobposting/search", "https://jobs.dayforcehcm.com/en-US/fixture/EXTERNAL/jobs/1", "https://foreign.example/fixture/EXTERNAL"} {
		if SecondaryMonitorResourceMatches(p, c, url) {
			t.Fatal("foreign resource accepted")
		}
	}
	for _, key := range []string{"proxy", "skip_ssl", "ssl_verify", "render", "actions", "offset_overlap"} {
		clone := cloneConfig(c)
		var m map[string]any
		json.Unmarshal([]byte(c["metadata"]), &m)
		switch key {
		case "proxy", "skip_ssl":
			m[key] = true
		case "ssl_verify", "render":
			m[key] = false
		case "actions":
			m[key] = []any{map[string]any{"action": "evaluate", "script": "fetch('foreign')"}}
		case "offset_overlap":
			m[key] = 25
		}
		b, _ := json.Marshal(m)
		clone["metadata"] = string(b)
		if _, e := InspectRichMonitor(profileBoardID, clone); e == nil {
			t.Fatal("unported configuration admitted", key)
		}
	}
}

func TestDayforceCurrentTenRegistryBoardsCompileToBrowserSkip(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	headers := map[string]int{}
	for i, h := range rows[0] {
		headers[h] = i
	}
	count := 0
	for _, row := range rows[1:] {
		if row[headers["monitor_type"]] != "dayforce" {
			continue
		}
		c := profileConfig()
		c["crawler_type"] = "dayforce"
		c["monitor_needs_browser"] = "1"
		c["board_url"] = row[headers["board_url"]]
		m := map[string]any{}
		if raw := row[headers["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &m) != nil {
			t.Fatal("invalid registry config")
		}
		m["scraper_type"] = row[headers["scraper_type"]]
		b, _ := json.Marshal(m)
		c["metadata"] = string(b)
		p, e := InspectRichMonitor(profileBoardID, c)
		if e != nil || p.Profile != dayforceMonitorProfile || MonitorWorker(p) != Browser {
			t.Fatal("current Dayforce board excluded", row[headers["board_slug"]], e)
		}
		count++
	}
	if count != 10 {
		t.Fatal("Dayforce registry coverage changed", count)
	}
}
