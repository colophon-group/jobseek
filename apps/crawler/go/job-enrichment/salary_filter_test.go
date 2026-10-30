package enrichment

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
	"time"
)

func checkSalaryFilter(t testing.TB, text string) {
	t.Helper()
	text = salaryText(text)
	for name := range salaryCurrencyMarkers {
		scan := newSalaryScan(text, time.Now().Add(5*time.Second))
		got, want := scan.matches(name, false), scan.scanMatches(name, false)
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("currency filter changed %s match spans or captures", name)
		}
	}
}

// Compare spans/captures against the unfiltered engine, in addition to final
// output parity. This catches a skipped family hidden by another winning range.
func TestSalaryCurrencyFiltersPreserveEveryOracleMatch(t *testing.T) {
	body, err := os.ReadFile("testdata/python_salary.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct{ Text string }
	if err := json.Unmarshal(body, &cases); err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		checkSalaryFilter(t, c.Text)
	}
}

func FuzzSalaryCurrencyFilters(f *testing.F) {
	for _, text := range []string{
		"Build reliable services, 5 years of experience.",
		"Salary €50000–60000 annually; CHF 120000 per year; £25 per hour; $25/hr",
		"Salary 100000 PLN; ZŁ 10000; Kč 50000; CZK 50000; 50000 KRONOR",
		"Salary 50000 ſEK; 50000 DKK; 50000 HUF; Ft 50000; 50000 RON; 50000 leİ; 50000 лв.; 50000 BGN",
		"Salary Â€ 50000, R$50000 yearly; ١٢٠٠٠٠ CHF; salary &euro; 50000",
		"salary 50000 EUR\x00gross; £25 per hour\n$30/hr",
	} {
		f.Add(text)
	}
	f.Fuzz(func(t *testing.T, text string) {
		if len(text) > 8192 {
			t.Skip()
		}
		checkSalaryFilter(t, text)
	})
}
