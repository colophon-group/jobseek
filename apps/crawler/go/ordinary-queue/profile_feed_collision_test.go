package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestCollisionPoliciesCurrentRegistryRetainProfileAndConfigurationFence(t *testing.T) {
	expected := map[string]string{"canton-of-fribourg-main": "rss.successfactors-skip/v1", "capgemini-frog": "dom.direct-rows/v1", "mediamarktsaturn-careers-global": "rss.successfactors-skip/v1", "chuv-careers": "api_sniffer.http-items/v1", "swiss-post-main": "rss.successfactors-skip/v1", "capgemini-global": "api_sniffer.http-items/v1"}
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	headers := map[string]int{}
	for i, k := range rows[0] {
		headers[k] = i
	}
	seen := 0
	for _, row := range rows[1:] {
		slug := row[headers["board_slug"]]
		want, ok := expected[slug]
		if !ok {
			continue
		}
		md := map[string]any{}
		if json.Unmarshal([]byte(row[headers["monitor_config"]]), &md) != nil {
			t.Fatal("invalid metadata")
		}
		md["scraper_type"] = row[headers["scraper_type"]]
		if raw := row[headers["scraper_config"]]; raw != "" {
			var detail any
			if json.Unmarshal([]byte(raw), &detail) != nil {
				t.Fatal("detail config")
			}
			md["scraper_config"] = detail
		}
		body, _ := json.Marshal(md)
		config := profileConfig()
		config["metadata"] = string(body)
		config["crawler_type"] = row[headers["monitor_type"]]
		config["board_url"] = row[headers["board_url"]]
		p, err := InspectRichMonitor(profileBoardID, config)
		if err != nil || p.Profile != want {
			t.Fatal("current registry collision is not qualified", slug, p.Profile, err)
		}
		rules, err := FeedMonitorURLRules(config)
		if err != nil || !rules.HasCollision() || !rules.RequiresRawInventory() {
			t.Fatal("collision did not require complete input", slug, err)
		}
		transform := md["url_transform"].(map[string]any)
		transform["collision_stream_buffer_limit"] = int(transform["collision_stream_buffer_limit"].(float64)) + 1
		body, _ = json.Marshal(md)
		config["metadata"] = string(body)
		changed, err := InspectRichMonitor(profileBoardID, config)
		if err != nil || changed.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
			t.Fatal("changed identity policy escaped canonical fence", slug, err)
		}
		seen++
	}
	if seen != len(expected) {
		t.Fatal("registry cohort changed", seen)
	}
}
