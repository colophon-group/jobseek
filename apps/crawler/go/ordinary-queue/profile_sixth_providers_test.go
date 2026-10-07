package queue

import (
	"encoding/csv"
	"encoding/json"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	"os"
	"strings"
	"testing"
)

func TestSixthProvidersCurrentRegistryCoverage(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil || len(rows) < 2 {
		t.Fatal("registry unavailable")
	}
	head := map[string]int{}
	for i, key := range rows[0] {
		head[key] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[head["monitor_type"]]
		if provider != "manatal" && provider != "hrmos" && provider != "recruiterbox" && provider != "jobs_ch" {
			continue
		}
		md := map[string]any{}
		raw := row[head["monitor_config"]]
		if raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("invalid monitor metadata")
		}
		if scraper := row[head["scraper_type"]]; scraper != "" {
			md["scraper_type"] = scraper
		}
		if raw := row[head["scraper_config"]]; raw != "" {
			var sc any
			if json.Unmarshal([]byte(raw), &sc) != nil {
				t.Fatal("invalid detail metadata")
			}
			md["scraper_config"] = sc
		}
		body, _ := json.Marshal(md)
		config := profileConfig()
		config["crawler_type"], config["board_url"], config["metadata"] = provider, row[head["board_url"]], string(body)
		p, e := InspectRichMonitor(profileBoardID, config)
		if e != nil || p.Provider != provider || MonitorWorker(p) != Simple {
			t.Fatal("provider unsupported", row[head["board_slug"]], e)
		}
		if !SecondaryMonitorResourceMatches(p, config, p.Endpoint) {
			t.Fatal("canonical resource unsupported")
		}
		if md["scraper_type"] == "json-ld" {
			job := ""
			switch provider {
			case "hrmos":
				job = "https://hrmos.co/pages/" + strings.Split(strings.TrimPrefix(p.Endpoint, "https://hrmos.co/pages/"), "/")[0] + "/jobs/123"
			case "recruiterbox":
				job = strings.Split(p.Endpoint, "?")[0] + "jobs/job123/"
			case "jobs_ch":
				o, err := api.JobCloudOptionsFromMetadata(config["board_url"], config["metadata"])
				if err != nil {
					t.Fatal(err)
				}
				host := "www.jobs.ch"
				if o.Portal == "jobup" {
					host = "www.jobup.ch"
				}
				job = "https://" + host + "/" + o.Locale + "/" + o.DetailPath() + "/detail/00000000-0000-0000-0000-000000000123/"
			}
			if job != "" {
				if _, err := InspectJSONLDDetail(profileBoardID, config, job, Simple); err != nil {
					t.Fatal("configured JSON-LD detail unsupported", row[head["board_slug"]], err)
				}
			}
		}
		counts[provider]++
	}
	if counts["manatal"] != 5 || counts["hrmos"] != 6 || counts["recruiterbox"] != 6 || counts["jobs_ch"] != 11 {
		t.Fatal("registry coverage changed", counts)
	}
	t.Logf("configuration eligibility only: %v", counts)
}

func TestSixthExtensionGoneAuthorityIsSourceBound(t *testing.T) {
	config := profileConfig()
	config["crawler_type"], config["board_url"], config["metadata"] = "recruiterbox", "https://tenant.recruiterbox.com/", `{"scraper_type":"json-ld"}`
	o, err := api.RecruiterboxOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil {
		t.Fatal(err)
	}
	if !SecondaryMonitorGone(config, o.PageURL(1), 404, false) || !SecondaryMonitorGone(config, o.PageURL(2), 200, true) || SecondaryMonitorGone(config, o.PageURL(2), 404, false) || SecondaryMonitorGone(config, "https://other.hire.trakstar.com/?limit=100&p=1", 200, true) {
		t.Fatal("listing gone authority differs")
	}
	config["crawler_type"], config["board_url"] = "jobs_ch", "https://www.jobs.ch/de/firmen/123-tenant/"
	j, err := api.JobCloudOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil {
		t.Fatal(err)
	}
	if SecondaryMonitorGone(config, j.SearchURL(1), 404, false) || SecondaryMonitorGone(config, j.SearchURL(1), 200, true) {
		t.Fatal("search failure became board tombstone")
	}
}
