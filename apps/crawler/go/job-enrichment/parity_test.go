package enrichment

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestFullPythonTaxonomyParity(t *testing.T) {
	m, err := Load("../../data")
	if err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile("testdata/python_taxonomy.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixtures struct {
		Titles []struct {
			Text       string
			Occupation *string
			Seniority  *string
		}
		Technologies []struct {
			Text  string
			Slugs []string
		}
		InternSignals []string `json:"intern_signals"`
	}
	if err = json.Unmarshal(body, &fixtures); err != nil {
		t.Fatal(err)
	}
	failures := 0
	for _, f := range fixtures.Titles {
		if actual := m.Occupation(f.Text); !reflect.DeepEqual(actual, f.Occupation) {
			t.Errorf("occupation %q: got %v want %v", f.Text, printable(actual), printable(f.Occupation))
			failures++
		}
		if actual := Seniority(f.Text); !reflect.DeepEqual(actual, f.Seniority) {
			t.Errorf("seniority %q: got %v want %v", f.Text, printable(actual), printable(f.Seniority))
			failures++
		}
		if failures >= 10 {
			t.FailNow()
		}
	}
	for _, f := range fixtures.Technologies {
		if actual := m.Technologies(f.Text); !reflect.DeepEqual(actual, f.Slugs) {
			t.Errorf("technology %q: got %v want %v", f.Text, actual, f.Slugs)
			failures++
		}
		if failures >= 10 {
			t.FailNow()
		}
	}
	if len(fixtures.InternSignals) != len(interns) {
		t.Fatal("intern signal drift")
	}
	for _, s := range fixtures.InternSignals {
		if !interns[s] {
			t.Fatalf("missing intern signal %q", s)
		}
	}
}
func printable(p *string) any {
	if p == nil {
		return nil
	}
	return *p
}
