package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestSharedDOMAPICurrentRegistryPreservesDeclaredTransports(t *testing.T) {
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
	supported := map[string]int{}
	retained := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[h["monitor_type"]]
		if provider != "dom" && provider != "api_sniffer" {
			continue
		}
		md := map[string]any{}
		if raw := row[h["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("monitor config", row[h["board_slug"]])
		}
		fields, _ := md["fields"].(map[string]any)
		candidate := provider == "api_sniffer" && len(fields) == 0 || provider == "dom" && (md["include_board_url"] == true || md["require_jsonld_jobposting"] == true)
		if !candidate {
			continue
		}
		md["scraper_type"] = row[h["scraper_type"]]
		if raw := row[h["scraper_config"]]; raw != "" {
			var value any
			if json.Unmarshal([]byte(raw), &value) != nil {
				t.Fatal("detail config")
			}
			md["scraper_config"] = value
		}
		body, _ := json.Marshal(md)
		config := profileConfig()
		config["crawler_type"], config["board_url"], config["metadata"] = provider, row[h["board_url"]], string(body)
		browser := provider == "dom" && md["render"] == true || provider == "api_sniffer" && md["browser"] == true
		if browser {
			config["monitor_needs_browser"] = "1"
		}
		profile, e := InspectRichMonitor(profileBoardID, config)
		if e != nil {
			retained[provider]++
			continue
		}
		if (MonitorWorker(profile) == Browser) != browser || ProfileRequiresProxy(profile.Profile) != (md["proxy"] == true) {
			t.Fatal("transport authority changed", row[h["board_slug"]])
		}
		if provider == "api_sniffer" {
			if browser {
				o, e := APISnifferBrowserMonitorOptions(config)
				if e != nil || !o.Inventory.AutoFields {
					t.Fatal("automatic browser mapping not bound", e)
				}
			} else {
				o, e := APISnifferMonitorOptions(config)
				if e != nil || !o.AutoFields {
					t.Fatal("automatic HTTP mapping not bound", e)
				}
			}
		} else {
			o, e := DOMMonitorOptions(config)
			if e != nil || o.IncludeBoardURL != (md["include_board_url"] == true) || o.RequireJSONLD != (md["require_jsonld_jobposting"] == true) {
				t.Fatal("DOM verification/direct-board options not bound", e)
			}
		}
		supported[provider]++
	}
	if supported["api_sniffer"] != 23 || supported["dom"] != 12 || retained["api_sniffer"] != 12 || retained["dom"] != 11 {
		t.Fatal("registry coverage empty", supported)
	}
	t.Logf("configuration binding only; production route admission pending: supported=%v retained=%v", supported, retained)
}
