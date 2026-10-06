package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"
)

func TestSecondaryProviderFieldsAndRoutesMatchActualPython(t *testing.T) {
	raw, err := os.ReadFile("../ordinary-worker/testdata/python_secondary_api_providers.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Softgarden []struct {
			Name, HTML string
			IDs        []string
		}
		UKGRoutes []struct {
			URL      string
			Expected map[string]string
		} `json:"ukg_routes"`
		UKGFields []struct {
			Name     string
			Raw      json.RawMessage
			Expected map[string]any
		} `json:"ukg_fields"`
		UKGPages []struct {
			Name     string
			Raw      json.RawMessage
			Expected map[string]any
		} `json:"ukg_pages"`
		BambooFields []struct {
			Name     string
			Raw      json.RawMessage
			Expected map[string]any
		} `json:"bamboo_fields"`
		BambooListing []struct {
			Name     string
			Raw      json.RawMessage
			Expected map[string]any
		} `json:"bamboo_listing"`
		UKGMetadata []struct {
			URL      string
			Metadata json.RawMessage
			Expected map[string]string
		} `json:"ukg_metadata"`
		RecruiterFields []struct {
			Name     string
			Detail   json.RawMessage
			Summary  map[string]any
			Expected map[string]any
		} `json:"recruiter_fields"`
		RecruiterDates []struct{ Value, Expected any } `json:"recruiter_dates"`
	}
	if err := json.Unmarshal(raw, &corpus); err != nil || len(corpus.Softgarden) != 8 || len(corpus.UKGRoutes) != 10 || len(corpus.UKGFields) != 24 || len(corpus.UKGPages) != 7 {
		t.Fatal("actual grouped Python corpus missing", err)
	}
	for _, c := range corpus.Softgarden {
		t.Run("softgarden/"+c.Name, func(t *testing.T) {
			o := SoftgardenOptions{Slug: "fixture", Pattern: "{base}/job/{id}?l=en"}
			got, truncated, err := SoftgardenListing(c.HTML, o)
			expected := []string{}
			seen := map[string]bool{}
			for _, id := range c.IDs {
				value := o.JobURL(id)
				if !seen[value] {
					seen[value] = true
					expected = append(expected, value)
				}
			}
			sort.Strings(got)
			sort.Strings(expected)
			if err != nil || truncated || !reflect.DeepEqual(got, expected) {
				t.Fatal("listing identities differ", got, expected, err)
			}
		})
	}
	for _, c := range corpus.UKGRoutes {
		t.Run("ukg/route/"+c.URL, func(t *testing.T) {
			b, err := UKGBoardFromURL(c.URL)
			if c.Expected == nil {
				if err == nil {
					t.Fatal("invalid route accepted")
				}
				return
			}
			if err != nil || b.Host != c.Expected["host"] || b.Tenant != c.Expected["tenant"] || b.BoardID != c.Expected["board_id"] {
				t.Fatal("board route differs", b, err)
			}
		})
	}
	board, err := UKGBoardFromURL(corpus.UKGRoutes[0].URL)
	if err != nil {
		t.Fatal(err)
	}
	normalize := func(value any) any {
		raw, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		var out any
		if err := json.Unmarshal(raw, &out); err != nil {
			t.Fatal(err)
		}
		return out
	}
	for _, c := range corpus.UKGFields {
		t.Run("ukg/fields/"+c.Name, func(t *testing.T) {
			d, err := Decode(c.Raw)
			if err != nil {
				t.Fatal(err)
			}
			got := UKGFields(d.Value, board)
			if !reflect.DeepEqual(normalize(got), normalize(c.Expected)) {
				t.Fatal("UKG projected fields differ", got, c.Expected)
			}
		})
	}
	for _, c := range corpus.UKGPages {
		t.Run("ukg/page/"+c.Name, func(t *testing.T) {
			d, err := Decode(c.Raw)
			if err != nil {
				t.Fatal(err)
			}
			total, rows, err := UKGPage(d)
			if c.Expected == nil {
				if err == nil {
					t.Fatal("invalid page accepted")
				}
				return
			}
			if err != nil || !reflect.DeepEqual(normalize(map[string]any{"total": total, "rows": rows}), c.Expected) {
				t.Fatal("UKG page differs", err)
			}
		})
	}
	if len(corpus.BambooFields) != 20 || len(corpus.BambooListing) != 10 || len(corpus.UKGMetadata) != 7 {
		t.Fatal("next grouped reference cases missing")
	}
	for _, c := range corpus.BambooFields {
		t.Run("bamboohr/fields/"+c.Name, func(t *testing.T) {
			d, err := Decode(c.Raw)
			if err != nil {
				t.Fatal(err)
			}
			got := BambooHRFields(d.Value, BambooHROptions{Tenant: "acme"})
			if !reflect.DeepEqual(normalize(got), normalize(c.Expected)) {
				t.Fatal("BambooHR fields differ", got, c.Expected)
			}
		})
	}
	for _, c := range corpus.BambooListing {
		t.Run("bamboohr/listing/"+c.Name, func(t *testing.T) {
			d, err := Decode(c.Raw)
			if err != nil {
				t.Fatal(err)
			}
			jobs, truncated, err := BambooHRListing(d, BambooHROptions{Tenant: "acme"})
			if c.Expected == nil {
				if err == nil {
					t.Fatal("invalid listing accepted")
				}
				return
			}
			if err != nil || !reflect.DeepEqual(normalize(map[string]any{"jobs": jobs, "truncated": truncated}), c.Expected) {
				t.Fatal("BambooHR inventory differs", err, jobs, c.Expected)
			}
		})
	}
	for _, c := range corpus.UKGMetadata {
		t.Run("ukg/config/"+string(c.Metadata), func(t *testing.T) {
			b, err := UKGOptionsFromMetadata(c.URL, string(c.Metadata))
			if c.Expected == nil {
				if err == nil {
					t.Fatal("invalid identity accepted")
				}
				return
			}
			if err != nil || b.Host != c.Expected["host"] || b.Tenant != c.Expected["tenant"] || b.BoardID != c.Expected["board_id"] {
				t.Fatal("metadata route differs", b, err)
			}
		})
	}
	if len(corpus.RecruiterFields) != 12 || len(corpus.RecruiterDates) != 12 {
		t.Fatal("Korean references missing")
	}
	for _, c := range corpus.RecruiterFields {
		t.Run("recruiter_co_kr/fields/"+c.Name, func(t *testing.T) {
			d, err := Decode(c.Detail)
			if err != nil {
				t.Fatal(err)
			}
			got, err := d.RecruiterKRFields(d.Value.(map[string]any), c.Summary)
			if err != nil || !reflect.DeepEqual(normalize(got), normalize(c.Expected)) {
				t.Fatal("Korean fields differ", err, got, c.Expected)
			}
		})
	}
	for _, c := range corpus.RecruiterDates {
		t.Run("recruiter_co_kr/date/"+stringValueForSecondaryTest(c.Value), func(t *testing.T) {
			if got := RecruiterKRDate(c.Value); !reflect.DeepEqual(got, c.Expected) {
				t.Fatal("Korean UTC date differs", c.Value, got, c.Expected)
			}
		})
	}
}

func stringValueForSecondaryTest(value any) string { raw, _ := json.Marshal(value); return string(raw) }

func TestSoftgardenTruncationUsesRawIDCountBeforeURLDeduplication(t *testing.T) {
	o := SoftgardenOptions{Slug: "fixture", Pattern: "{base}/job/{id}?l=en"}
	for _, count := range []int{50000, 50001} {
		html := "var complete_job_id_list=[" + strings.Repeat("1,", count) + "];"
		urls, truncated, err := SoftgardenListing(html, o)
		if err != nil || len(urls) != 1 || truncated != (count > 50000) {
			t.Fatal("raw-count safety boundary differs", count, truncated, err)
		}
	}
}
