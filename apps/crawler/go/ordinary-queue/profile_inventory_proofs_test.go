package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestDOMAPIProofsQualifyCurrentRegistryContracts(t *testing.T) {
	targets := map[string]bool{"university-of-geneva-cvu": true, "general-motors-careers-ultium-troy": true, "ferring-pharmaceuticals-careers-cz": true, "european-professional-club-rugby-careers": true, "university-of-geneva-physics-paruch": true, "securitas-vietnam": true, "integrated-resources-iri-career-portal": true, "securitas-south-korea": true, "geneva-centre-for-security-policy-main": true, "securitas-slovakia": true, "international-commission-of-jurists-careers": true}
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	head := map[string]int{}
	for i, key := range rows[0] {
		head[key] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		slug := row[head["board_slug"]]
		if !targets[slug] {
			continue
		}
		md := map[string]any{}
		if raw := row[head["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("registry metadata")
		}
		md["scraper_type"] = row[head["scraper_type"]]
		if raw := row[head["scraper_config"]]; raw != "" {
			var v any
			if json.Unmarshal([]byte(raw), &v) != nil {
				t.Fatal("detail metadata")
			}
			md["scraper_config"] = v
		}
		body, _ := json.Marshal(md)
		config := profileConfig()
		config["board_url"], config["crawler_type"], config["metadata"] = row[head["board_url"]], row[head["monitor_type"]], string(body)
		if md["render"] == true || md["browser"] == true {
			config["monitor_needs_browser"] = "1"
		}
		p, err := InspectRichMonitor(profileBoardID, config)
		if err != nil || p.Provider != config["crawler_type"] {
			t.Fatal("current proof configuration rejected", slug, err)
		}
		counts[p.Provider]++
		delete(targets, slug)
	}
	if len(targets) != 0 || counts["dom"] != 9 || counts["api_sniffer"] != 2 {
		t.Fatal("proof coverage changed", counts, targets)
	}
}
