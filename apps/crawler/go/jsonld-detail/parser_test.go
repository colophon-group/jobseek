package jsonld

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

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
