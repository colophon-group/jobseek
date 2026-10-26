package worker

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Explicit public qualification complements the hermetic source/digest guards.
// Captures contain canonical identities and remain in the protected evidence
// directory; normal CI exercises source scoping and the real canonical writer.
func TestRetainedControlSourcesMatchOriginalAndPhysicalDetails(t *testing.T) {
	dir := os.Getenv("JOBSEEK_RETAINED1098_DIR")
	if dir == "" {
		t.Skip("protected original and official physical references required")
	}
	qualified := map[string]int{
		"alten-belgium": 3, "alten-italy": 3, "alten-netherlands": 3,
		"alten-portugal": 3, "alten-switzerland": 3,
		"bright-horizons-only-about-children-australia": 3,
		"glasswall-careers":                             3, "unhcr-thailand": 1,
	}
	raw, err := os.ReadFile(filepath.Join(dir, "positive-original-details1.private.json"))
	var originals []struct {
		ID      string
		Config  map[string]string
		Samples []struct {
			URL, Status string
			Content     map[string]any
		}
	}
	if err != nil || json.Unmarshal(raw, &originals) != nil {
		t.Fatal("original positive details unavailable")
	}
	for engine, sha := range map[string]string{
		"101": "d4b66ac8484f49d7a49e05b6f9342716c747129527fcbe8ffe36679ffa18dd60",
		"102": "9f3ee749a55d1abc5f8fdda0d9559c3b2abcec5bff5bfe902bc06c0a89538ec4",
	} {
		raw, err := os.ReadFile(filepath.Join(dir, "physical"+engine+"-details2.private.json"))
		var physical struct {
			EngineSHA256 string `json:"engine_sha256"`
			Records      []struct {
				Slug    string
				Samples []struct {
					URL, HTML string
					FinalURL  string `json:"final_url"`
					Status    int
					Complete  bool
				}
			}
		}
		if err != nil || json.Unmarshal(raw, &physical) != nil || physical.EngineSHA256 != sha {
			t.Fatal("official physical engine reference unavailable")
		}
		seen := 0
		for _, original := range originals {
			slug := original.Config["board_slug"]
			count, ok := qualified[slug]
			if !ok {
				continue
			}
			seen++
			t.Run(engine+"/"+slug, func(t *testing.T) {
				if len(original.Samples) != count {
					t.Fatal("positive source sample count changed")
				}
				var md map[string]any
				json.Unmarshal([]byte(original.Config["metadata"]), &md)
				options := md["scraper_config"].(map[string]any)
				for n, sample := range original.Samples {
					matched := false
					for _, record := range physical.Records {
						if record.Slug != slug || len(record.Samples) != count {
							continue
						}
						live := record.Samples[n]
						if sample.Status != "complete" || !live.Complete || live.Status != 200 || live.URL != sample.URL || sample.Content["title"] == nil || sample.Content["description"] == nil {
							t.Fatal("positive bound physical detail missing")
						}
						p, err := queue.InspectRenderedDetail(original.ID, original.Config, sample.URL, queue.Browser)
						if err != nil || p.BrowserProxy != (options["proxy"] == true) {
							t.Fatal("original exact source or configured proxy binding lost", err)
						}
						content, _, err := parseHeldDetail(context.Background(), p, heldRenderedResult(live.HTML, live.FinalURL, 200))
						if err != nil {
							t.Fatal("bound held detail failed", err)
						}
						raw, _ := json.Marshal(content)
						var actual map[string]any
						json.Unmarshal(raw, &actual)
						for _, field := range []string{"title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary", "language", "metadata", "extras"} {
							if !reflect.DeepEqual(actual[field], sample.Content[field]) {
								t.Fatal("original field differs", n, field)
							}
						}
						matched = true
					}
					if !matched {
						t.Fatal("physical source missing")
					}
				}
			})
		}
		if seen != len(qualified) {
			t.Fatal("qualified source omitted")
		}
	}
}
