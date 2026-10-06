package queue

import (
	"encoding/csv"
	"encoding/json"
	"net/url"
	"os"
	"testing"
)

func TestProviderBatchCurrentRegistryConfigurationCoverage(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil || len(rows) < 2 {
		t.Fatal("registry unavailable")
	}
	headers := map[string]int{}
	for i, key := range rows[0] {
		headers[key] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[headers["monitor_type"]]
		if provider != "mokahr" && provider != "almacareer" && provider != "eightfold" {
			continue
		}
		metadata := map[string]any{}
		if raw := row[headers["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &metadata) != nil {
			t.Fatal("invalid provider configuration", row[headers["board_slug"]])
		}
		if scraper := row[headers["scraper_type"]]; scraper != "" {
			metadata["scraper_type"] = scraper
		}
		if scraper := row[headers["scraper_config"]]; scraper != "" {
			var value any
			if json.Unmarshal([]byte(scraper), &value) != nil {
				t.Fatal("invalid detail configuration")
			}
			metadata["scraper_config"] = value
		}
		encoded, e := json.Marshal(metadata)
		if e != nil {
			t.Fatal("configuration cannot be serialized")
		}
		config := profileConfig()
		config["crawler_type"], config["board_url"], config["metadata"] = provider, row[headers["board_url"]], string(encoded)
		profile, e := InspectRichMonitor(profileBoardID, config)
		if metadata["proxy"] == true {
			if e == nil {
				t.Fatal("configured proxy acquired direct monitor authority")
			}
			counts[provider+"_proxy_preserved"]++
			continue
		}
		if e != nil || profile.Provider != provider || MonitorWorker(profile) != Simple {
			t.Fatal("configured direct provider unsupported", row[headers["board_slug"]])
		}
		counts[provider]++
		scraper, _ := metadata["scraper_type"].(string)
		if scraper == "skip" {
			continue
		}
		var source string
		if provider == "mokahr" {
			o, e := MokahrMonitorOptions(config)
			if e != nil {
				t.Fatal("missing MokaHR route")
			}
			source = o.Partitions[0].JobURL("OWNERSHIPADMISSION")
		}
		if provider == "eightfold" {
			u, e := url.Parse(config["board_url"])
			if e != nil {
				t.Fatal("missing Eightfold route")
			}
			source = "https://" + u.Host + "/careers/job/1?domain=" + url.QueryEscape(u.Hostname())
		}
		if source != "" {
			p, e := InspectAPIDetail(profileBoardID, config, source, Simple)
			if e != nil || p.EffectiveBoardSHA256 != profile.EffectiveConfigSHA256 {
				t.Fatal("required provider detail unsupported", row[headers["board_slug"]])
			}
			if _, e := inspectDetailOwnership(profileBoardID, config); e != nil {
				t.Fatal("required detail owner unsupported")
			}
			counts[provider+"_detail"]++
		}
	}
	if counts["mokahr"] == 0 || counts["almacareer"] == 0 || counts["eightfold"] == 0 {
		t.Fatal("registry coverage fixture empty")
	}
	t.Logf("configuration eligibility only (no production authority): %v", counts)
}

func TestEightfoldStableBindingPreservesOperatorConfigAndIgnoresOnlyRuntimeState(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["board_url"], c["metadata"] = "eightfold", "https://fixture.eightfold.ai/careers", `{"scraper_type":"eightfold"}`
	p, e := InspectRichMonitor(profileBoardID, c)
	if e != nil {
		t.Fatal(e)
	}
	c["metadata"] = `{"scraper_type":"eightfold","pcsx_watermark":{"max_ts":123,"last_full_at":"2026-10-06T03:00:00Z","enabled":true,"interval_days":7,"auto_full_crawl":true,"extra":{"host":"fixture.eightfold.ai","domain":"fixture"}}}`
	advanced, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || advanced.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 || advanced.SnapshotSHA256 == p.SnapshotSHA256 {
		t.Fatal("runtime state invalidated stable owner or erased exact snapshot", e)
	}
	for _, change := range []string{`{"scraper_type":"eightfold","pcsx_force_full_crawl":true}`, `{"scraper_type":"eightfold","pcsx_watermark":{"auto_full_crawl":false}}`, `{"scraper_type":"eightfold","pcsx_watermark":{"interval_days":2}}`} {
		c["metadata"] = change
		changed, e := InspectRichMonitor(profileBoardID, c)
		if e != nil || changed.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
			t.Fatal("operator setting lost immutable binding", e)
		}
	}
	for _, change := range []string{`{"scraper_type":"eightfold","pcsx_watermark":{"unknown":true}}`, `{"scraper_type":"eightfold","proxy":true}`, `{"scraper_type":"eightfold","render":true}`, `{"scraper_type":"eightfold","sitemap_url":"https://foreign.example/sitemap.xml"}`} {
		c["metadata"] = change
		if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
			t.Fatal("unsupported source acquired direct authority")
		}
	}
}
