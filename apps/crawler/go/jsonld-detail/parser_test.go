package jsonld

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestEnfraICIMSLocationsAndSalary(t *testing.T) {
	for _, c := range []struct {
		file             string
		locations        []string
		minimum, maximum float64
	}{
		{"enfra_icims_hourly.html", []string{"Newark, NJ, US"}, 17.43, 23.31},
		{"enfra_icims_multilocation.html", []string{"Denver, CO, US", "Tucson, AZ, US", "Salt Lake City, UT, US", "Tempe, AZ, US"}, 88380, 117970},
	} {
		t.Run(c.file, func(t *testing.T) {
			raw, err := os.ReadFile("testdata/" + c.file)
			if err != nil {
				t.Fatal(err)
			}
			content, err := Parse("https://careers-enfra.icims.com/jobs/1/job?in_iframe=1", raw, nil)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(content["locations"], c.locations) {
				t.Fatalf("locations: %v", content["locations"])
			}
			encoded, err := json.Marshal(content["base_salary"])
			if err != nil {
				t.Fatal(err)
			}
			var actual map[string]any
			if err := json.Unmarshal(encoded, &actual); err != nil {
				t.Fatal(err)
			}
			expected := map[string]any{"currency": "USD", "min": c.minimum, "max": c.maximum, "unit": nil}
			if !reflect.DeepEqual(actual, expected) {
				t.Fatalf("salary: got %v, want %v", actual, expected)
			}
		})
	}
}

func TestFrozenPythonParser(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name     string         `json:"name"`
		Request  Request        `json:"request"`
		Expected map[string]any `json:"expected"`
		Error    string         `json:"expected_error"`
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			actual, err := Parse(c.Request.URL, []byte(c.Request.HTML), c.Request.Config)
			if c.Error != "" {
				if err == nil {
					t.Fatal("expected error")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			raw, err := json.Marshal(actual)
			if err != nil {
				t.Fatal(err)
			}
			var normalized map[string]any
			if err := json.Unmarshal(raw, &normalized); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(normalized, c.Expected) {
				t.Fatalf("got %s\nwant %v", raw, c.Expected)
			}
		})
	}
}
