package enrichment

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestPythonHTMLParity(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_html.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixtures struct {
		Cases []struct {
			Text string  `json:"text"`
			HTML *string `json:"html"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &fixtures); err != nil {
		t.Fatal(err)
	}
	if len(fixtures.Cases) < 6000 {
		t.Fatal("incomplete HTML oracle")
	}
	failures := 0
	for _, c := range fixtures.Cases {
		actual, err := NormalizeDescriptionHTML(c.Text)
		if err != nil || !reflect.DeepEqual(actual, c.HTML) {
			t.Errorf("HTML %q: got %v error %v want %v", c.Text, printable(actual), err, printable(c.HTML))
			failures++
			if failures == 10 {
				t.FailNow()
			}
		}
	}
}
