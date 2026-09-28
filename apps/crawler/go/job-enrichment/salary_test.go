package enrichment

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestSalaryPythonParity(t *testing.T) {
	body, err := os.ReadFile("testdata/python_salary.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Text    string
		Rates   map[string]float64
		Ranges  []SalaryRange
		Unified *SalaryRange
		Parsed  *ParsedSalary
		EUR     *int64
	}
	if err = json.Unmarshal(body, &cases); err != nil {
		t.Fatal(err)
	}
	for i, c := range cases {
		got, err := Salary(c.Text, c.Rates)
		if err != nil {
			t.Errorf("case %d %q: %v", i, c.Text, err)
			continue
		}
		// Interface numbers decode as float64; compare their serialized values.
		expected := SalaryResult{c.Ranges, c.Unified, c.Parsed, c.EUR}
		a, _ := json.Marshal(got)
		b, _ := json.Marshal(expected)
		if string(a) != string(b) {
			t.Errorf("case %d %q:\ngot %s\nwant%s", i, c.Text, a, b)
		}
	}
}
func TestSalaryFailureIsExplicit(t *testing.T) {
	for _, text := range []string{"US, CA, Anywhere - " + strings.Repeat("9", 400) + " - 30 USD hourly", "$" + strings.Repeat("9", 400) + "/year"} {
		result, err := Salary(text, nil)
		if err == nil || result != nil {
			t.Fatal("unrepresentable salary must fail without partial output")
		}
	}
	result, err := Salary("Salary CHF 120000 - 100000 yearly", map[string]float64{"CHF": 1})
	if err != nil || result.Unified.Max == nil || *result.Unified.Max != 100000 {
		t.Fatal("existing reversed CHF range policy changed")
	}
}
func TestSalaryHourlyCentsAndEUR(t *testing.T) {
	result, err := Salary("$25.51/hr", map[string]float64{"USD": 0.9})
	if err != nil || result.Unified.Min != 2551 || result.EUR == nil || *result.EUR != 47755 {
		t.Fatalf("cents/EUR result: %+v %v", result, err)
	}
}
