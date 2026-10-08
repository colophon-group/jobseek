package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestAutomaticAPIArrayCurrentExplicitFieldRegistryBindings(t *testing.T) {
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
	for i, k := range rows[0] {
		h[k] = i
	}
	count := 0
	for _, r := range rows[1:] {
		if r[h["monitor_type"]] != "api_sniffer" {
			continue
		}
		md := map[string]any{}
		if json.Unmarshal([]byte(r[h["monitor_config"]]), &md) != nil {
			continue
		}
		if md["json_path"] != nil || md["url_field"] == nil || md["fields"] == nil || md["browser"] == true || md["render"] == true || md["proxy"] == true {
			continue
		}
		md["scraper_type"] = r[h["scraper_type"]]
		if raw := r[h["scraper_config"]]; raw != "" {
			var value any
			if json.Unmarshal([]byte(raw), &value) != nil {
				t.Fatal("detail options")
			}
			md["scraper_config"] = value
		}
		c := profileConfig()
		c["crawler_type"], c["domain"], c["throttle_key"], c["board_url"] = "api_sniffer", "api_sniffer", "api_sniffer", r[h["board_url"]]
		body, _ := json.Marshal(md)
		c["metadata"] = string(body)
		p, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || p.Profile != "api_sniffer.http-items/v1" {
			t.Fatal(r[h["board_slug"]], err, p)
		}
		o, err := APISnifferMonitorOptions(c)
		if err != nil || !o.AutoPath || !APISnifferMonitorResourceMatches(p, c, p.Endpoint) {
			t.Fatal("automatic path/source binding", err)
		}
		delete(md, "fields")
		body, _ = json.Marshal(md)
		c["metadata"] = string(body)
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("automatic field inference admitted")
		}
		count++
	}
	if count != 11 {
		t.Fatal("registry coverage changed", count)
	}
}
