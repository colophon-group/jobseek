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

type preparationLocations struct {
	raw            []string
	kind, language string
	err            error
}

func (l *preparationLocations) Resolve(ctx context.Context, raw []string, kind, language string) ([]int64, []string, error) {
	l.raw, l.kind, l.language = raw, kind, language
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	if l.err != nil {
		return nil, nil, l.err
	}
	if len(raw) == 0 {
		return nil, nil, nil
	}
	return []int64{2}, []string{"hybrid"}, nil
}

func TestPreparedContentMatchesPythonOrdinaryAndEnrichTransactions(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_prepare.json")
	if err != nil {
		t.Fatal(err)
	}
	var oracle struct {
		Occupations, Seniorities, Technologies map[string]int64
		Rates                                  map[string]float64
		Cases                                  []struct {
			Name            string
			Content, Config map[string]any
			Existing        *struct {
				Titles         []string
				LocationIDs    []int64 `json:"location_ids"`
				EmploymentType *string `json:"employment_type"`
			}
			Expected struct {
				Success        bool
				Disposition    string
				Fields         json.RawMessage
				LocationInputs []json.RawMessage `json:"location_inputs"`
				Description    *struct {
					HTML, Locale string
					Hash         int64
				}
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
			var existing *EnrichSnapshot
			if c.Existing != nil {
				existing = &EnrichSnapshot{Titles: c.Existing.Titles, LocationIDs: c.Existing.LocationIDs, EmploymentType: c.Existing.EmploymentType}
			}
			prepared, err := processor.Prepare(context.Background(), c.Content, c.Config, existing)
			if !c.Expected.Success {
				if err == nil || FailureClass(err) != FailureTransient {
					t.Fatal("empty or invalid content did not take transient path")
				}
				return
			}
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
				t.Fatalf("nullable persistence fields differ:\n%s\n%s", actualJSON, c.Expected.Fields)
			}
			if c.Expected.Description == nil {
				if prepared.Description != nil {
					t.Fatal("unexpected description upload")
				}
			} else {
				candidate := prepared.Description
				if candidate == nil || candidate.HTML != c.Expected.Description.HTML || candidate.Locale != c.Expected.Description.Locale || candidate.Hash != c.Expected.Description.Hash {
					t.Fatalf("description bytes/locale/hash differ: %+v", candidate)
				}
			}
			if len(c.Expected.LocationInputs) > 0 {
				var raw []string
				var kind, language *string
				if json.Unmarshal(c.Expected.LocationInputs[0], &raw) != nil || json.Unmarshal(c.Expected.LocationInputs[1], &kind) != nil || json.Unmarshal(c.Expected.LocationInputs[2], &language) != nil {
					t.Fatal("invalid location oracle")
				}
				if !reflect.DeepEqual(locations.raw, raw) || locations.kind != optionalText(kind) || locations.language != optionalText(language) {
					t.Fatal("location input coercion differs")
				}
			}
		})
	}
}

func TestPreparedContentPropagatesLocationFailure(t *testing.T) {
	matcher, err := enrichment.Load("../../data")
	if err != nil {
		t.Fatal(err)
	}
	cause := errors.New("location index unavailable")
	p := Processor{Matcher: matcher, Lookups: &NativeLookups{}, Locations: &preparationLocations{err: cause}}
	_, err = p.Prepare(context.Background(), map[string]any{"title": "Engineer"}, nil, nil)
	if !errors.Is(err, cause) {
		t.Fatal("location failure became successful content")
	}
}
