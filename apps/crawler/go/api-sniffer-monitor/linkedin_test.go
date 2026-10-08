package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strconv"
	"testing"
)

func linkedInNullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}
func linkedInComparisonJob(j Job) map[string]any {
	return map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "date_posted": j.DatePosted, "locations": j.Locations, "metadata": j.Metadata, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "extras": j.Extras}
}
func linkedInEqualJSON(a, b any) bool {
	x, _ := json.Marshal(a)
	y, _ := json.Marshal(b)
	return string(x) == string(y)
}

func TestLinkedInActualPythonCardsScopeRequestsAndDiscovery(t *testing.T) {
	var corpus struct {
		Parsers []struct {
			Name, Body      string
			Numeric, Failed bool
			Jobs            []map[string]any
		}
		Empty []struct {
			Body  string
			Empty bool
		}
		Inventories []struct {
			Name              string
			BoardURL          string `json:"board_url"`
			Metadata          map[string]any
			Responses         []*string
			Jobs              []map[string]any
			Failed, Truncated bool
			Calls             []struct {
				URL     string
				Headers map[string]string
			}
		}
	}
	raw, err := os.ReadFile("testdata/python_linkedin.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Parsers) != 30 || len(corpus.Empty) != 11 || len(corpus.Inventories) != 27 {
		t.Fatal("Python LinkedIn corpus absent", err)
	}
	for _, c := range corpus.Parsers {
		t.Run("cards/"+c.Name+"/"+strconv.FormatBool(c.Numeric), func(t *testing.T) {
			jobs, err := ParseLinkedInCards([]byte(c.Body), c.Numeric)
			if (err != nil) != c.Failed {
				t.Fatal(err, c.Failed)
			}
			if err != nil {
				return
			}
			out := []map[string]any{}
			for _, j := range jobs {
				out = append(out, map[string]any{"job_id": j.ID, "url": j.URL, "title": linkedInNullable(j.Title), "date_posted": linkedInNullable(j.DatePosted), "locations": j.Locations, "company_slug": linkedInNullable(j.CompanySlug), "location_country_code": nil})
			}
			if !linkedInEqualJSON(out, c.Jobs) {
				t.Fatal(out, c.Jobs)
			}
		})
	}
	for i, c := range corpus.Empty {
		t.Run("empty/"+strconv.Itoa(i), func(t *testing.T) {
			if got := linkedInEmptyFragment([]byte(c.Body)); got != c.Empty {
				t.Fatal(c.Body, got, c.Empty)
			}
		})
	}
	for _, c := range corpus.Inventories {
		t.Run("inventory/"+c.Name, func(t *testing.T) {
			md, _ := json.Marshal(c.Metadata)
			o, err := LinkedInOptionsFromMetadata(c.BoardURL, string(md))
			if err != nil {
				if !c.Failed || len(c.Calls) > 0 {
					t.Fatal("option failure differs", err, c.Calls)
				}
				return
			}
			calls := []string{}
			out, err := DiscoverLinkedIn(context.Background(), o, func(ctx context.Context, r Request) ([]byte, error) {
				i := len(calls)
				calls = append(calls, r.URL)
				if i >= len(c.Calls) || i >= len(c.Responses) {
					t.Error("unexpected request", r.URL)
					return nil, errors.New("unexpected request")
				}
				want := c.Calls[i]
				if r.Method != "GET" || r.URL != want.URL || r.Body != "" {
					t.Error("request differs", r, want)
				}
				for k, v := range want.Headers {
					if r.Headers.Get(k) != v {
						t.Error("header differs", k, r.Headers, want.Headers)
					}
				}
				body := c.Responses[i]
				if body == nil {
					return nil, nil
				}
				if *body == "__failure__" {
					return nil, errors.New("later fixture request failed")
				}
				return []byte(*body), nil
			}, func(ctx context.Context) error { return ctx.Err() })
			if (err != nil) != c.Failed || len(calls) != len(c.Calls) || err != nil && len(out.Jobs) > 0 {
				t.Fatal("outcome differs", err, c.Failed, len(out.Jobs), len(calls), len(c.Calls))
			}
			if err != nil {
				return
			}
			actual := []map[string]any{}
			want := []map[string]any{}
			for _, j := range out.Jobs {
				actual = append(actual, linkedInComparisonJob(j))
			}
			for _, j := range c.Jobs {
				fields := map[string]any{}
				for k := range linkedInComparisonJob(Job{}) {
					fields[k] = j[k]
				}
				want = append(want, fields)
			}
			if !linkedInEqualJSON(actual, want) || out.Truncated != c.Truncated {
				t.Fatal("fields or partial semantics differ", len(actual), len(want), out.Truncated, c.Truncated, reflect.DeepEqual(actual, want))
			}
		})
	}
}
