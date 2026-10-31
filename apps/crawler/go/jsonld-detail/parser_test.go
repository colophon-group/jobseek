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

func TestStarhotelsExactURLSalarySuppression(t *testing.T) {
	const target = "https://starhotels.talosats-careers.com/job/972748"
	raw, err := os.ReadFile("testdata/starhotels_talos_salary_conflict.html")
	if err != nil {
		t.Fatal(err)
	}
	config := map[string]any{"ignore_base_salary_urls": []any{target}}
	if err := ValidateConfig(config); err != nil {
		t.Fatal(err)
	}
	without, err := Parse(target, raw, nil)
	if err != nil || without["base_salary"] == nil {
		t.Fatalf("source salary missing before suppression: %v, %v", without, err)
	}
	selected, err := Parse(target, raw, config)
	if err != nil {
		t.Fatal(err)
	}
	if selected["base_salary"] != nil || selected["title"] != without["title"] || !reflect.DeepEqual(selected["locations"], without["locations"]) || selected["description"] != without["description"] || selected["employment_type"] != without["employment_type"] || selected["date_posted"] != without["date_posted"] {
		t.Fatalf("suppression changed another field: %v", selected)
	}
	withDefault, err := Parse(target, raw, map[string]any{
		"ignore_base_salary_urls": []any{target},
		"defaults_by_url":         map[string]any{target: map[string]any{"base_salary": map[string]any{"currency": "GBP", "min": 1}}},
	})
	if err != nil || withDefault["base_salary"] != nil {
		t.Fatalf("default reintroduced a suppressed salary: %v, %v", withDefault, err)
	}
	other, err := Parse("https://starhotels.talosats-careers.com/job/977321", raw, config)
	if err != nil || !reflect.DeepEqual(other["base_salary"], without["base_salary"]) {
		t.Fatalf("other posting lost salary: %v, %v", other, err)
	}
	for _, invalid := range []any{"x", []any{"http://example.com/job/1"}, []any{target, target}, []any{42}} {
		if err := ValidateConfig(map[string]any{"ignore_base_salary_urls": invalid}); err == nil {
			t.Fatalf("accepted invalid salary suppression: %v", invalid)
		}
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
