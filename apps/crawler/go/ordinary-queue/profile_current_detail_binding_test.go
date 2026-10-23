package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestCurrentDetailBindingsPreserveProviderWorkerAndOriginalControls(t *testing.T) {
	var cases []struct {
		Slug    string `json:"board_slug"`
		Source  string `json:"source_url"`
		Profile string `json:"expected_profile"`
		Worker  WorkerType
	}
	body, err := os.ReadFile("testdata/current_detail_binding_cases.json")
	if err != nil || json.Unmarshal(body, &cases) != nil || len(cases) != 2 {
		t.Fatal("current source binding cases unavailable", err)
	}
	file, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	rows, err := csv.NewReader(file).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	for _, test := range cases {
		t.Run(test.Slug, func(t *testing.T) {
			var row []string
			for _, candidate := range rows[1:] {
				if candidate[1] == test.Slug {
					row = candidate
					break
				}
			}
			if row == nil {
				t.Fatal("configured board disappeared")
			}
			var metadata map[string]json.RawMessage
			if json.Unmarshal([]byte(row[4]), &metadata) != nil {
				t.Fatal("monitor configuration unavailable")
			}
			metadata["scraper_type"], _ = json.Marshal(row[5])
			metadata["scraper_config"] = json.RawMessage(row[6])
			config := profileConfig()
			config["board_slug"], config["board_url"], config["crawler_type"] = row[1], row[2], row[3]
			config["scraper_needs_browser"] = "0"
			if test.Worker == Browser {
				config["scraper_needs_browser"] = "1"
			}
			inspect := InspectWorkdayDetail
			if test.Worker == Browser {
				inspect = InspectRenderedDetail
			}
			encoded, err := json.Marshal(metadata)
			if err != nil {
				t.Fatal(err)
			}
			config["metadata"] = string(encoded)
			profile, err := inspect(profileBoardID, config, test.Source, test.Worker)
			if err != nil || profile.Profile != test.Profile || profile.SourceURL != test.Source || len(profile.EffectiveBoardSHA256) != 64 {
				t.Fatal("current complete source binding rejected", err, profile.Profile)
			}
			otherWorker := Browser
			if test.Worker == Browser {
				otherWorker = Simple
			}
			if _, err := inspect(profileBoardID, config, test.Source, otherWorker); err == nil {
				t.Fatal("source crossed its independent queue lane")
			}
			if test.Worker == Simple {
				metadata["scraper_type"] = json.RawMessage(`"onlyfy"`)
				metadata["scraper_config"] = json.RawMessage(`{"language":"en"}`)
			} else {
				var scraper map[string]json.RawMessage
				if json.Unmarshal(metadata["scraper_config"], &scraper) != nil {
					t.Fatal("original parser configuration unavailable")
				}
				scraper["stealth"] = json.RawMessage(`true`)
				metadata["scraper_config"], _ = json.Marshal(scraper)
			}
			encoded, _ = json.Marshal(metadata)
			config["metadata"] = string(encoded)
			if _, err := inspect(profileBoardID, config, test.Source, test.Worker); err == nil {
				t.Fatal("prior unqualified configuration gained blanket native ownership")
			}
		})
	}
}
