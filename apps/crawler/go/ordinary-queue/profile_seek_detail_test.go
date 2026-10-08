package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestSeekDetailCanonicalRegistryBindings(t *testing.T) {
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
	for i, key := range rows[0] {
		h[key] = i
	}
	count := 0
	for _, row := range rows[1:] {
		if row[h["monitor_type"]] != "seek" {
			continue
		}
		md := map[string]any{}
		if row[h["monitor_config"]] != "" && json.Unmarshal([]byte(row[h["monitor_config"]]), &md) != nil {
			t.Fatal("monitor metadata")
		}
		md["scraper_type"] = row[h["scraper_type"]]
		options := map[string]any{}
		if row[h["scraper_config"]] != "" && json.Unmarshal([]byte(row[h["scraper_config"]]), &options) != nil {
			t.Fatal("detail metadata")
		}
		md["scraper_config"] = options
		c := profileConfig()
		c["crawler_type"], c["board_url"] = "seek", row[h["board_url"]]
		body, _ := json.Marshal(md)
		c["metadata"] = string(body)
		p, err := inspectDetailOwnership(profileBoardID, c)
		if err != nil || p.Profile != seekDetailProfile || p.Domain != "*" || !independentDetailProfile(p.Profile) {
			t.Fatal(row[h["board_slug"]], err, p)
		}
		// The GraphQL endpoint identifies the canonical market for the sample.
		source := p.Endpoint[:len(p.Endpoint)-len("graphql")] + "job/123"
		detail, err := InspectSeekDetail(profileBoardID, c, source, Simple)
		if err != nil || detail.EffectiveBoardSHA256 != p.EffectiveBoardSHA256 || detail.HTTPAPIConfig["advertiser_id"] == "" {
			t.Fatal(err, detail)
		}
		foreign := "https://nz.seek.com/job/123"
		if detail.Domain == "nz.seek.com" {
			foreign = "https://au.seek.com/job/123"
		}
		for _, invalid := range []string{foreign, "https://example.net/job/123", source + "/other", source[:len(source)-3] + "%31%32%33"} {
			if _, err := InspectSeekDetail(profileBoardID, c, invalid, Simple); err == nil {
				t.Fatal("foreign source admitted", invalid)
			}
		}
		if _, err := InspectSeekDetail(profileBoardID, c, source, Browser); err == nil {
			t.Fatal("browser admitted")
		}
		for _, key := range []string{"advertiser_id", "api_url", "query", "proxy", "render", "unknown"} {
			md["scraper_config"] = map[string]any{key: "999999999"}
			body, _ := json.Marshal(md)
			c["metadata"] = string(body)
			if _, err := inspectDetailOwnership(profileBoardID, c); err == nil {
				t.Fatal("unbound detail option admitted", key)
			}
		}
		count++
	}
	if count != 6 {
		t.Fatal("registry coverage changed", count)
	}
}
