package worker

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestGroupedRSSProviderItemsMatchActualPython(t *testing.T) {
	var corpus struct {
		Cases []struct {
			Name, Preset, Body string
			Jobs               []map[string]any
		}
	}
	body, err := os.ReadFile("testdata/python_rss_provider_items.json")
	if err != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) != 16 {
		t.Fatal("actual Python RSS reference corpus unavailable", err)
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			found, err := parseRSSProvider([]byte(c.Body), c.Preset)
			if err != nil || len(found.Jobs) != len(c.Jobs) {
				t.Fatal("item inventory differs", err, len(found.Jobs), len(c.Jobs))
			}
			for n, job := range found.Jobs {
				actual := map[string]any{"url": job.URL, "title": job.Title, "description": job.Description, "locations": job.Locations, "metadata": job.Metadata, "employment_type": job.EmploymentType, "date_posted": job.DatePosted, "source_identity": nullableNextdataIdentity(job.SourceIdentity)}
				b, _ := json.Marshal(actual)
				var decoded map[string]any
				_ = json.Unmarshal(b, &decoded)
				for key, value := range actual {
					_ = value
					if !reflect.DeepEqual(decoded[key], c.Jobs[n][key]) {
						t.Errorf("actual Python %s differs: got=%v want=%v", key, decoded[key], c.Jobs[n][key])
					}
				}
			}
		})
	}
}

func TestRSSProviderParsingNeverReturnsPartialMalformedInventory(t *testing.T) {
	for _, preset := range []string{"governmentjobs", "zoho_recruit"} {
		for _, raw := range []string{`<rss><item><link>https://example.com/one</link></item><item>`, `<rss></rss><rss></rss>`, `<html>challenge</html>`, `<rss><item><link>https://example.com/one</link></item></rss>junk`} {
			found, err := parseRSSProvider([]byte(raw), preset)
			if err == nil || len(found.Jobs) != 0 {
				t.Fatalf("malformed %s inventory accepted: %v", preset, err)
			}
		}
	}
}
