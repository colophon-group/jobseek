package queue

import (
	"encoding/json"
	"os"
	"testing"
)

func TestOrderedDetailActualStaticConfigurations(t *testing.T) {
	var rows []struct {
		Slug          string         `json:"slug"`
		CrawlerType   string         `json:"crawler_type"`
		BoardURL      string         `json:"board_url"`
		ScraperType   string         `json:"scraper_type"`
		Browser       string         `json:"browser"`
		ScraperConfig map[string]any `json:"scraper_config"`
		Expected      bool           `json:"expected"`
	}
	body, err := os.ReadFile("testdata/ordered_detail_configs.json")
	if err != nil || json.Unmarshal(body, &rows) != nil {
		t.Fatal("actual configured fallback projection unavailable", err)
	}
	if len(rows) != 9 {
		t.Fatal("configured chain coverage changed", len(rows))
	}
	for _, row := range rows {
		t.Run(row.Slug, func(t *testing.T) {
			config := jsonldDetailConfig()
			config["crawler_type"], config["board_url"], config["board_slug"], config["scraper_needs_browser"] = row.CrawlerType, row.BoardURL, row.Slug, row.Browser
			metadata := map[string]any{"scraper_type": row.ScraperType, "scraper_config": row.ScraperConfig}
			encoded, _ := json.Marshal(metadata)
			config["metadata"] = string(encoded)
			profile, err := InspectOrderedDetail(jsonldBoardID, config, row.BoardURL, Simple)
			if !row.Expected {
				if err == nil {
					t.Fatal("mixed browser lanes admitted without handoff qualification")
				}
				return
			}
			if err != nil || len(profile.Stages) != 2 || profile.EffectiveBoardSHA256 == "" {
				t.Fatal("actual static chain unsupported", err, profile)
			}
			for _, stage := range profile.Stages {
				if stage.EffectiveBoardSHA256 != profile.EffectiveBoardSHA256 || stage.SourceURL != row.BoardURL || stage.CompanyID != config["company_id"] {
					t.Fatal("stage detached from original board/source")
				}
			}
			if row.Slug == "loreal-careers" && (profile.Stages[0].Profile != domProxyDetailProfile || profile.Stages[1].Profile != jsonldDetailProfile) {
				t.Fatal("per-stage sealed transport changed", profile.Stages)
			}
		})
	}
}

func TestOrderedDetailRejectsUnqualifiedStages(t *testing.T) {
	for _, cfg := range []string{
		`{"fallback":{"type":"dom","config":{"render":true}}}`,
		`{"fallback":{"type":"unknown"}}`,
		`{"fallback":{"type":"dom","config":{"proxy":true,"proxy":false}}}`,
		`{"fallback":{"type":"dom","fields":"description"}}`,
		`{"fallback":{"type":"dom","unexpected":1}}`,
		`{"enrich":["description"],"fallback":{"type":"dom"}}`,
	} {
		config := jsonldDetailConfig()
		config["metadata"] = `{"scraper_type":"json-ld","scraper_config":` + cfg + `}`
		if _, err := InspectOrderedDetail(jsonldBoardID, config, "https://jobs.example/detail", Simple); err == nil {
			t.Fatal("unqualified chain admitted", cfg)
		}
	}
}
