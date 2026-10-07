package worker

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestGroupedRSSVariantItemsMatchActualPython(t *testing.T) {
	var corpus struct {
		Cases []struct {
			Name, Kind, Body string
			Jobs             []map[string]any
			Error            bool
		}
		Identities []struct {
			Source   string
			Expected []string
		}
		Streams []struct {
			Name, Kind, Body string
			Error            bool
			URLs             []string `json:"urls"`
			BatchSizes       []int    `json:"batch_sizes"`
		} `json:"stream_cases"`
	}
	raw, err := os.ReadFile("testdata/python_rss_variants.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Cases) != 24 || len(corpus.Identities) != 7 {
		t.Fatal("actual Python variant reference unavailable", err)
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			var found RichDiscovery
			var err error
			if c.Kind == "legacy_xml" {
				found, err = parseSFLegacyXML([]byte(c.Body), "https://career.example.com", "Fixture_Company")
			} else {
				found, err = parseGenericStructuredSummary([]byte(c.Body))
			}
			if (err != nil) != c.Error || len(found.Jobs) != len(c.Jobs) {
				t.Fatal("Python variant terminal/inventory differs", err, c.Error, len(found.Jobs), len(c.Jobs))
			}
			for i, job := range found.Jobs {
				fields := map[string]any{"url": job.URL, "title": job.Title, "description": job.Description, "locations": job.Locations, "metadata": job.Metadata, "employment_type": job.EmploymentType, "date_posted": job.DatePosted, "job_location_type": job.JobLocationType}
				b, _ := json.Marshal(fields)
				var got map[string]any
				_ = json.Unmarshal(b, &got)
				for key, value := range got {
					if !reflect.DeepEqual(value, c.Jobs[i][key]) {
						t.Errorf("Python %s differs: got=%v want=%v", key, value, c.Jobs[i][key])
					}
				}
			}
		})
	}
	for _, c := range corpus.Identities {
		origin, company, err := sfLegacyXMLIdentity(c.Source)
		if c.Expected == nil {
			if err == nil {
				t.Fatal("invalid tenant feed admitted")
			}
			continue
		}
		if err != nil || origin != c.Expected[0] || company != c.Expected[1] {
			t.Fatal("strict XML tenant identity differs", origin, company, err)
		}
	}
	if len(corpus.Streams) != 9 {
		t.Fatal("actual Python RSS streams unavailable")
	}
	for _, c := range corpus.Streams {
		t.Run(c.Name, func(t *testing.T) {
			var found RichDiscovery
			var err error
			if c.Kind == "legacy_xml" {
				found, err = parseSFLegacyXML([]byte(c.Body), "https://career.example.com", "Fixture_Company")
			} else {
				found, err = parseGenericStructuredSummary([]byte(c.Body))
			}
			if (err != nil) != c.Error || len(found.Jobs) != len(c.URLs) {
				t.Fatal("actual Python committed stream prefix differs", err, len(found.Jobs), len(c.URLs))
			}
			for i, job := range found.Jobs {
				if job.URL != c.URLs[i] {
					t.Fatal("stream URL differs", i, job.URL, c.URLs[i])
				}
			}
		})
	}
}
