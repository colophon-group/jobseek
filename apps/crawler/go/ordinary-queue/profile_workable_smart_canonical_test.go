package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestWorkableProxyProfilesBindIndependentTransport(t *testing.T) {
	c := apiMonitorConfig("workable")
	c["metadata"] = `{"token":"fixture","proxy":true,"scraper_type":"workable","scraper_config":{"proxy":true}}`
	c["scraper_needs_browser"] = "0"
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "workable.proxy-api-urls/v1" || !ProfileRequiresProxy(p.Profile) || MonitorWorker(p) != Simple {
		t.Fatal("required proxy monitor lost transport", p, err)
	}
	d, err := InspectAPIDetail(profileBoardID, c, "https://apply.workable.com/fixture/j/ABC123/", Simple)
	if err != nil || d.Profile != workableProxyDetailProfile || !ProfileRequiresProxy(d.Profile) {
		t.Fatal("independent proxy detail lost transport", d, err)
	}
	c["metadata"] = `{"token":"fixture","proxy":true,"scraper_type":"workable"}`
	direct, err := InspectAPIDetail(profileBoardID, c, "https://apply.workable.com/fixture/j/ABC123/", Simple)
	if err != nil || ProfileRequiresProxy(direct.Profile) || direct.EffectiveBoardSHA256 == d.EffectiveBoardSHA256 {
		t.Fatal("monitor proxy leaked into independently configured detail", direct, err)
	}
}

func TestSmartCanonicalMonitorBindsModesAndPublicationResources(t *testing.T) {
	for _, mode := range []string{`"canonical_identity":"job-v1"`, `"canonical_identity":"job-location-v1"`, `"canonical_job_id_url_template":"https://career.hm.com/job/{job_id}/"`} {
		c := apiMonitorConfig("smartrecruiters")
		c["scraper_needs_browser"] = "0"
		c["metadata"] = `{"scraper_type":"skip",` + mode + `}`
		p, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || p.Profile != "smartrecruiters.canonical-items/v1" || ProfileRequiresProxy(p.Profile) {
			t.Fatal("canonical rich mode not bound", p, err)
		}
		if !APIMonitorResourceMatches(p, "https://api.smartrecruiters.com/v1/companies/fixture/postings/123") {
			t.Fatal("required tenant-bound publication detail rejected")
		}
		for _, resource := range []string{"https://api.smartrecruiters.com/v1/companies/other/postings/123", "https://jobs.smartrecruiters.com/fixture/123", "https://api.smartrecruiters.com/v1/companies/fixture/postings/123?x=1", "https://api.smartrecruiters.com/v1/companies/fixture/postings/%2e%2e"} {
			if APIMonitorResourceMatches(p, resource) {
				t.Fatal("foreign publication resource admitted", resource)
			}
		}
		c["metadata"] = `{"scraper_type":"json-ld","scraper_config":{"enrich":["description"]},` + mode + `}`
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unimplemented canonical detail delegation admitted")
		}
	}
}

func TestWorkableProxyAndSmartCanonicalCurrentRegistryConfigurations(t *testing.T) {
	expected := map[string]string{"abm-uk": "workable.proxy-api-urls/v1", "fuku-careers": "workable.proxy-api-urls/v1", "vertiv-bmarko": "workable.proxy-api-urls/v1", "hm-group-careers-group": "smartrecruiters.canonical-items/v1", "nagarro-careers": "smartrecruiters.canonical-items/v1", "swiss-medical-network-smartrecruiters": "smartrecruiters.canonical-items/v1"}
	file, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	rows, err := csv.NewReader(file).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	h := map[string]int{}
	for i, key := range rows[0] {
		h[key] = i
	}
	for _, row := range rows[1:] {
		slug := row[h["board_slug"]]
		want, present := expected[slug]
		if !present {
			continue
		}
		t.Run(slug, func(t *testing.T) {
			var md map[string]any
			if json.Unmarshal([]byte(row[h["monitor_config"]]), &md) != nil {
				t.Fatal("metadata")
			}
			md["scraper_type"] = row[h["scraper_type"]]
			var sc map[string]any
			if raw := row[h["scraper_config"]]; raw != "" {
				if json.Unmarshal([]byte(raw), &sc) != nil {
					t.Fatal("detail config")
				}
				md["scraper_config"] = sc
			}
			body, _ := json.Marshal(md)
			config := profileConfig()
			config["crawler_type"], config["board_url"], config["metadata"] = row[h["monitor_type"]], row[h["board_url"]], string(body)
			p, err := InspectRichMonitor(profileBoardID, config)
			if err != nil || p.Profile != want || ProfileRequiresProxy(p.Profile) != (md["proxy"] == true) {
				t.Fatal("actual configured monitor lost transport/identity", p, err)
			}
			if p.Provider == "workable" {
				config["domain"] = "apply.workable.com"
				d, err := InspectAPIDetail(profileBoardID, config, "https://apply.workable.com/"+p.Token+"/j/ABC123/", Simple)
				if err != nil || ProfileRequiresProxy(d.Profile) != (sc["proxy"] == true) {
					t.Fatal("actual detail assignment changed transport", d, err)
				}
			}
		})
		delete(expected, slug)
	}
	if len(expected) != 0 {
		t.Fatal("current registry cases missing", expected)
	}
}
