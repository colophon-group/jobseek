package worker

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestDiscoveredStringSalaryMatchesOriginalParserCorpus(t *testing.T) {
	body, e := os.ReadFile("../job-enrichment/testdata/python_salary.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Text   string
		Parsed any
	}
	if json.Unmarshal(body, &cases) != nil || len(cases) < 30 {
		t.Fatal("original salary corpus unavailable")
	}
	for i, c := range cases {
		actual, e := normalizeDiscoveredSalary(c.Text)
		if e != nil || !reflect.DeepEqual(actual, c.Parsed) {
			t.Fatalf("original discovered salary case %d differs", i)
		}
	}
	for _, value := range []any{nil, map[string]any{"currency": "CHF", "min": 123000, "unit": "year"}, []any{"retained value"}, false} {
		actual, e := normalizeDiscoveredSalary(value)
		if e != nil || !reflect.DeepEqual(actual, value) {
			t.Fatal("original non-string salary changed")
		}
	}
}
