package executor

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"testing"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

func TestRichMonitorPreparationMatchesPythonBoardWriter(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_rich_monitor.json")
	if err != nil {
		t.Fatal(err)
	}
	var oracle struct {
		Occupations, Seniorities, Technologies map[string]int64
		Rates                                  map[string]float64
		Cases                                  []struct {
			Name    string
			Content struct {
				Title, Description *string
				Locations          []string
				Language           any
			}
			Expected struct {
				Fields         json.RawMessage
				LocationInputs []json.RawMessage `json:"location_inputs"`
				Description    *DescriptionCandidate
			}
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&oracle); err != nil {
		t.Fatal(err)
	}
	matcher, err := enrichment.Load("../../data")
	if err != nil {
		t.Fatal(err)
	}
	lookups := &NativeLookups{occupations: oracle.Occupations, seniorities: oracle.Seniorities, technologies: oracle.Technologies, rates: oracle.Rates}
	for _, c := range oracle.Cases {
		t.Run(c.Name, func(t *testing.T) {
			locations := &preparationLocations{}
			processor := Processor{Matcher: matcher, Lookups: lookups, Locations: locations}
			prepared, err := processor.PrepareRichMonitor(context.Background(), RichMonitorContent(c.Content))
			if err != nil {
				t.Fatal(err)
			}
			actualJSON, err := json.Marshal(prepared.Fields.args("unused")[1:])
			if err != nil {
				t.Fatal(err)
			}
			var actual, expected any
			if json.Unmarshal(actualJSON, &actual) != nil || json.Unmarshal(c.Expected.Fields, &expected) != nil {
				t.Fatal("invalid field fixture")
			}
			if !reflect.DeepEqual(actual, expected) {
				t.Fatalf("rich nullable fields differ:\n%s\n%s", actualJSON, c.Expected.Fields)
			}
			if prepared.Enrich || !reflect.DeepEqual(prepared.Description, c.Expected.Description) {
				t.Fatalf("rich description bytes/locale/hash differ: %+v / %+v", prepared.Description, c.Expected.Description)
			}
			var expectedLocations []string
			var kind, language *string
			if json.Unmarshal(c.Expected.LocationInputs[0], &expectedLocations) != nil || json.Unmarshal(c.Expected.LocationInputs[1], &kind) != nil || json.Unmarshal(c.Expected.LocationInputs[2], &language) != nil {
				t.Fatal("invalid location oracle")
			}
			if !reflect.DeepEqual(locations.raw, expectedLocations) || locations.kind != optionalText(kind) || locations.language != optionalText(language) {
				t.Fatalf("location inputs differ: %+v", locations)
			}
		})
	}
}

func TestRichMonitorPreparationRejectsCanceledAndUnavailableLookups(t *testing.T) {
	matcher, err := enrichment.Load("../../data")
	if err != nil {
		t.Fatal(err)
	}
	cause := errors.New("location index unavailable")
	p := &Processor{Matcher: matcher, Lookups: &NativeLookups{}, Locations: &preparationLocations{err: cause}}
	if prepared, err := p.PrepareRichMonitor(context.Background(), RichMonitorContent{}); prepared != nil || !errors.Is(err, cause) {
		t.Fatal("location failure became prepared content")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if prepared, err := p.PrepareRichMonitor(ctx, RichMonitorContent{}); prepared != nil || !errors.Is(err, context.Canceled) {
		t.Fatal("canceled preparation accepted content")
	}
	if prepared, err := (*Processor)(nil).PrepareRichMonitor(context.Background(), RichMonitorContent{}); prepared != nil || err == nil {
		t.Fatal("unavailable processor accepted content")
	}
}
