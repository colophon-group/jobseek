package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestTenthProvidersCurrentRegistryCoverageAndCanonicalDetailBindings(t *testing.T) {
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
	for n, k := range rows[0] {
		h[k] = n
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[h["monitor_type"]]
		if !TenthProvider(provider) {
			continue
		}
		md := map[string]any{}
		if raw := row[h["monitor_config"]]; raw != "" {
			if json.Unmarshal([]byte(raw), &md) != nil {
				t.Fatal("registry metadata malformed")
			}
		}
		md["scraper_type"] = row[h["scraper_type"]]
		if raw := row[h["scraper_config"]]; raw != "" {
			var value any
			if json.Unmarshal([]byte(raw), &value) != nil {
				t.Fatal("scraper metadata malformed")
			}
			md["scraper_config"] = value
		}
		body, _ := json.Marshal(md)
		c := profileConfig()
		c["crawler_type"], c["board_url"], c["metadata"] = provider, row[h["board_url"]], string(body)
		p, err := InspectRichMonitor(profileBoardID, c)
		if err != nil || p.Provider != provider || MonitorWorker(p) != Simple || ProfileRequiresProxy(p.Profile) || !SecondaryMonitorResourceMatches(p, c, p.Endpoint) || SecondaryMonitorResourceMatches(p, c, "https://foreign.example/private") {
			t.Fatal("enabled provider contract lost", row[h["board_slug"]], err)
		}
		if provider == "intervieweb" || provider == "typify" {
			d, err := inspectDetailOwnership(profileBoardID, c)
			if err != nil || d.EffectiveBoardSHA256 != p.EffectiveConfigSHA256 {
				t.Fatal("delegated detail binding lost", row[h["board_slug"]], err)
			}
		}
		c["monitor_needs_browser"] = "1"
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("browser contract downgraded")
		}
		counts[provider]++
	}
	if counts["intervieweb"] != 3 || counts["typify"] != 3 || counts["universia"] != 1 || counts["talentreef"] != 2 {
		t.Fatal("four-provider batch lost enabled configs", counts)
	}
}

func TestTenthDurableSourceIdentityRequiresBoardClientAndExactPublicPosting(t *testing.T) {
	board := "11111111-1111-4111-8111-111111111111"
	job := "22222222-2222-4222-8222-222222222222"
	source := "https://www.universia.net/es/empleo/" + job + "/engineer.html?referer=" + board
	for _, c := range []struct {
		provider, source, identity string
		valid                      bool
	}{
		{"universia", source, "universia:" + board + ":" + job, true},
		{"universia", source, "universia:" + job + ":" + job, false},
		{"universia", source, "universia:" + board + ":" + board, false},
		{"universia", "https://www.universia.net/es/empleo/fake/engineer.html?referer=fake", "universia:fake:fake", false},
		{"talentreef", "https://apply.jobappnetwork.com/clients/123/posting/17/en", "talentreef:123:17", true},
		{"talentreef", "https://apply.jobappnetwork.com/clients/123/posting/17/en", "talentreef:124:17", false},
		{"talentreef", "https://apply.jobappnetwork.com/clients/123/posting/17/en", "talentreef:123:18", false},
		{"talentreef", "https://foreign.example/clients/123/posting/17/en", "talentreef:123:17", false},
		{"talentreef", "https://apply.jobappnetwork.com/clients/fake/posting/17/en", "talentreef:fake:17", false},
	} {
		if validTenthSourceIdentity(c.provider, c.source, c.identity) != c.valid {
			t.Fatal("durable identity boundary changed", c)
		}
	}
}
