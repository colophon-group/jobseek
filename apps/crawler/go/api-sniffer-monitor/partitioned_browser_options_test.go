package apisniffer

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestPartitionedBrowserFactoriesAllConfiguredBoards(t *testing.T) {
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	indices := map[string]int{}
	for i, k := range rows[0] {
		indices[k] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[indices["monitor_type"]]
		if provider != "accenture" && provider != "brassring" {
			continue
		}
		board, raw := row[indices["board_url"]], row[indices["monitor_config"]]
		t.Run(row[indices["board_slug"]], func(t *testing.T) {
			var err error
			var o BrowserReplayOptions
			if provider == "accenture" {
				var a AccentureOptions
				a, o, err = AccentureBrowserOptions(board, raw)
				if a.Endpoint == AccentureJobSearch && o.Inventory.Body != "" {
					t.Fatal("captured request manufactured")
				}
			} else {
				_, o, err = BrassRingBrowserOptions(board, raw)
				if !strings.HasSuffix(o.Inventory.Endpoint, "/TGnewUI/Search/Ajax/MatchedJobs") {
					t.Fatal("provider application path lost")
				}
			}
			if err != nil || o.ResponseBodyLimit != 16<<20 {
				t.Fatal("configured board refused", err)
			}
			var m map[string]any
			json.Unmarshal([]byte(raw), &m)
			for _, control := range []string{"proxy", "stealth", "persistent_context", "request_headers", "response_body_limit", "actions"} {
				m[control] = true
				v, _ := json.Marshal(m)
				if provider == "accenture" {
					_, _, err = AccentureBrowserOptions(board, string(v))
				} else {
					_, _, err = BrassRingBrowserOptions(board, string(v))
				}
				if err == nil {
					t.Fatal("unsupported control discarded", control)
				}
				delete(m, control)
			}
		})
		counts[provider]++
	}
	if counts["accenture"] != 12 || counts["brassring"] != 4 {
		t.Fatal("configured census changed", counts)
	}
}

func TestBrassRingFactoryIdentityAndNavigationBounds(t *testing.T) {
	board := "https://sjobs.brassring.com/TGnewUI/Search/Home/Home?partnerid=25416&siteid=5998"
	for _, raw := range []string{`{"partner_id":"9"}`, `{"site_id":"9"}`, `{"wait":"unknown"}`, `{"timeout":0}`, `{"timeout":120001}`, `{"timeout":true}`} {
		if _, _, e := BrassRingBrowserOptions(board, raw); e == nil {
			t.Fatal("invalid identity/navigation admitted", raw)
		}
	}
	_, o, e := BrassRingBrowserOptions(board, `{"wait":"networkidle","timeout":90000}`)
	if e != nil || o.Wait != "networkidle" || o.TimeoutMS != 90000 {
		t.Fatal("original navigation controls lost", e)
	}
}
