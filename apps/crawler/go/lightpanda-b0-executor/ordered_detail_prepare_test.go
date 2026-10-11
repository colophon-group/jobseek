package executor

import (
	"context"
	"errors"
	"testing"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

func TestOrderedDetailPreparationRetainsPartialStageSemantics(t *testing.T) {
	matcher, err := enrichment.Load("../../data")
	if err != nil {
		t.Fatal(err)
	}
	p := &Processor{Matcher: matcher, Lookups: &NativeLookups{occupations: map[string]int64{}, seniorities: map[string]int64{}, technologies: map[string]int64{}, rates: map[string]float64{}}, Locations: &preparationLocations{}}
	for _, step := range []int{-1, 8} {
		if _, err := p.PrepareOrderedDetail(context.Background(), nil, step); err == nil {
			t.Fatal("unbounded stage accepted")
		}
	}
	if _, err := p.PrepareOrderedDetail(context.Background(), map[string]any{"description": "Description only"}, 0); !errors.Is(err, ErrEmptyResult) {
		t.Fatal("primary missing-title gate changed", err)
	}
	for _, content := range []map[string]any{{}, {"description": "<p>Description only</p>"}} {
		prepared, err := p.PrepareOrderedDetail(context.Background(), content, 1)
		if err != nil || !prepared.Enrich || prepared.Fields.Titles != nil {
			t.Fatal("partial stage overwrites missing title or consumes failure budget", prepared, err)
		}
		if len(content) == 0 && (prepared.Fields.Locales != nil || prepared.Description != nil) {
			t.Fatal("empty later stage invented a locale or description")
		}
	}
	prepared, err := p.PrepareOrderedDetail(context.Background(), map[string]any{"title": "Software Engineer", "description": "<p>Build useful software.</p>"}, 0)
	if err != nil || !prepared.Enrich || len(prepared.Fields.Titles) != 1 || prepared.Description == nil {
		t.Fatal("primary full content did not select the original COALESCE writer", prepared, err)
	}
}
