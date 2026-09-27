package enrichment

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestExperiencePythonParity(t *testing.T) {
	body, err := os.ReadFile("testdata/python_experience.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Text     string
		Min, Max *float64
	}
	if err = json.Unmarshal(body, &cases); err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		min, max := Experience(c.Text)
		if !reflect.DeepEqual(min, c.Min) || !reflect.DeepEqual(max, c.Max) {
			t.Errorf("%q: got %v/%v want %v/%v", c.Text, printableFloat(min), printableFloat(max), printableFloat(c.Min), printableFloat(c.Max))
		}
	}
}
func printableFloat(v *float64) any {
	if v == nil {
		return nil
	}
	return *v
}
