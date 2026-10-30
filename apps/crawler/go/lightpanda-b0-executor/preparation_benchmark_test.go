package executor

import (
	"context"
	"encoding/json"
	"os"
	"testing"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

// Frozen inputs keep this comparison independent of upstream and database load.
func BenchmarkRichMonitorPreparation(b *testing.B) {
	raw, err := os.ReadFile("testdata/python_rich_monitor.json")
	if err != nil {
		b.Fatal(err)
	}
	var oracle struct {
		Occupations, Seniorities, Technologies map[string]int64
		Rates                                  map[string]float64
		Cases                                  []struct{ Content json.RawMessage }
	}
	if err := json.Unmarshal(raw, &oracle); err != nil {
		b.Fatal(err)
	}
	matcher, err := enrichment.Load("../../data")
	if err != nil {
		b.Fatal(err)
	}
	p := Processor{Matcher: matcher, Lookups: &NativeLookups{occupations: oracle.Occupations, seniorities: oracle.Seniorities, technologies: oracle.Technologies, rates: oracle.Rates}, Locations: &preparationLocations{}}
	var inputs []RichMonitorContent
	for _, c := range oracle.Cases {
		var content struct {
			Title, Description  *string
			Locations           []string
			Language            any
			LocalizedTitles     []string       `json:"localized_titles"`
			LocalizationLocales []string       `json:"localization_locales"`
			EmploymentType      any            `json:"employment_type"`
			JobLocationType     any            `json:"job_location_type"`
			Extras              map[string]any `json:"extras"`
		}
		if err := json.Unmarshal(c.Content, &content); err != nil {
			b.Fatal(err)
		}
		inputs = append(inputs, RichMonitorContent{Title: content.Title, Description: content.Description, Locations: content.Locations, Language: content.Language, LocalizedTitles: content.LocalizedTitles, LocalizationLocales: content.LocalizationLocales, EmploymentType: content.EmploymentType, JobLocationType: content.JobLocationType, Extras: content.Extras})
	}
	for _, input := range inputs {
		if _, err := p.PrepareRichMonitor(context.Background(), input); err != nil {
			b.Fatal(err)
		}
	}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, err := p.PrepareRichMonitor(context.Background(), inputs[i%len(inputs)]); err != nil {
			b.Fatal(err)
		}
	}
}
