package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestNinthProvidersCurrentRegistryCoverage(t *testing.T) {
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
		if provider != "beehire" && provider != "hirehive" && provider != "welcometothejungle" && provider != "computrabajo" && provider != "ycombinator" {
			continue
		}
		md := map[string]any{}
		if raw := row[head["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("invalid registry metadata")
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
			t.Fatal("existing provider configuration unsupported", provider, e)
		}
		if ProfileRequiresProxy(p.Profile) != (md["proxy"] == true) {
			t.Fatal("proxy authority changed")
		}
		if !SecondaryMonitorResourceMatches(p, config, p.Endpoint) {
			t.Fatal("resource/gone contract differs")
		}
		if SecondaryMonitorResourceMatches(p, config, "https://example.com/other") {
			t.Fatal("foreign endpoint admitted")
		}
		if provider == "computrabajo" || provider == "ycombinator" {
			o, err := api.NinthProviderOptionsFromMetadata(provider, config["board_url"], config["metadata"])
			if err != nil {
				t.Fatal(err)
			}
			source := o.Origin + "/Detail/123"
			if provider == "ycombinator" {
				source = o.ListingURL() + "/A1-engineer"
			} else if o.Variant == "employer" {
				source = o.Origin + "/ofertas-de-trabajo/oferta-de-trabajo-de-engineer-11111111111111111111111111111111"
			}
			var detail WorkdayDetailProfile
			if md["scraper_type"] == "dom" {
				detail, err = InspectDOMDetail(profileBoardID, config, source, Simple)
			} else {
				detail, err = InspectJSONLDDetail(profileBoardID, config, source, Simple)
			}
			if err != nil || detail.EffectiveBoardSHA256 != p.EffectiveConfigSHA256 {
				t.Fatal("configured delegated detail binding lost", row[head["board_slug"]], err)
			}
		}
		config["monitor_needs_browser"] = "1"
		if _, e := InspectRichMonitor(profileBoardID, config); e == nil {
			t.Fatal("browser requirement ignored")
		}
		counts[provider]++
	}
	if counts["beehire"] != 1 || counts["hirehive"] != 1 || counts["welcometothejungle"] != 3 || counts["computrabajo"] != 11 || counts["ycombinator"] != 4 {
		t.Fatal("registry coverage changed", counts)
	}
}
