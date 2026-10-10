package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"reflect"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestRemainingHTTPCanonicalRegistryAndDetails(t *testing.T) {
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
	for i, key := range rows[0] {
		head[key] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[head["monitor_type"]]
		if !RemainingHTTPProvider(provider) {
			continue
		}
		t.Run(row[head["board_slug"]], func(t *testing.T) {
			md := map[string]any{}
			if json.Unmarshal([]byte(row[head["monitor_config"]]), &md) != nil {
				t.Fatal("registry metadata")
			}
			md["scraper_type"] = row[head["scraper_type"]]
			var detail any
			if json.Unmarshal([]byte(row[head["scraper_config"]]), &detail) != nil {
				t.Fatal("detail metadata")
			}
			md["scraper_config"] = detail
			raw, _ := json.Marshal(md)
			config := profileConfig()
			config["crawler_type"], config["board_url"], config["metadata"] = provider, row[head["board_url"]], string(raw)
			p, e := InspectRichMonitor(profileBoardID, config)
			if e != nil || p.Provider != provider || MonitorWorker(p) != Simple || ProfileRequiresProxy(p.Profile) != (md["proxy"] == true) {
				t.Fatal("monitor binding", e)
			}
			o, e := api.RemainingHTTPOptionsFromMetadata(provider, config["board_url"], config["metadata"])
			if e != nil || !SecondaryMonitorResourceMatches(p, config, o.ListingURL()) || SecondaryMonitorResourceMatches(p, config, "https://foreign.example/jobs") {
				t.Fatal("resource scope", e)
			}
			d, e := inspectDetailOwnership(profileBoardID, config)
			if e != nil || !independentDetailProfile(d.Profile) || d.Domain != "*" || d.EffectiveBoardSHA256 != p.EffectiveConfigSHA256 {
				t.Fatal("canonical detail ownership", e)
			}
			source := map[string]string{"johdi": config["board_url"] + "#/offer/23/job", "jobdiva": "https://www2.jobdiva.com/portal/?a=" + o.Tenant + "&compid=0#/jobs/23", "headhunter": "https://" + o.Host + "/vacancy/23"}[provider]
			claimed, e := inspectDetail(profileBoardID, config, source, Simple)
			if e != nil || claimed.Profile != d.Profile || claimed.EffectiveBoardSHA256 != d.EffectiveBoardSHA256 || ProfileRequiresProxy(claimed.Profile) != (provider == "headhunter") {
				t.Fatal("actual detail route", e)
			}
			if _, e = inspectDetail(profileBoardID, config, "https://foreign.example/jobs/23", Simple); e == nil && provider != "jobdiva" {
				t.Fatal("foreign detail accepted")
			}
			for _, patch := range []map[string]any{{"render": true}, {"unknown": true}, {"proxy": "true"}} {
				bad := map[string]any{}
				for k, v := range md {
					bad[k] = v
				}
				for k, v := range patch {
					bad[k] = v
				}
				b, _ := json.Marshal(bad)
				rejected := cloneConfig(config)
				rejected["metadata"] = string(b)
				if _, e = InspectRichMonitor(profileBoardID, rejected); e == nil {
					t.Fatal("unsupported config admitted")
				}
			}
		})
		counts[provider]++
	}
	if !reflect.DeepEqual(counts, map[string]int{"johdi": 2, "jobdiva": 2, "headhunter": 2}) {
		t.Fatal("registry coverage changed", counts)
	}
}
