package dom

import (
	"bytes"
	"encoding/json"
	"os"
	"reflect"
	"strconv"
	"testing"
)

// These are frozen outputs from the Python extraction and scraper regression
// suites. No interpreter, network, browser, or publisher traffic is needed.
func TestCanonicalPythonCorpus(t *testing.T) {
	for _, path := range []string{"testdata/python_cases.json", "testdata/python_render_policy.json"} {
		t.Run(path, func(t *testing.T) { testCanonicalCorpus(t, path) })
	}
}

func testCanonicalCorpus(t *testing.T, path string) {
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Request struct {
			Mode          string    `json:"mode"`
			HTML          string    `json:"html"`
			Config        Object    `json:"config"`
			URL           *string   `json:"url"`
			Elements      []Element `json:"elements"`
			Steps         []Object  `json:"steps"`
			Start         int       `json:"start"`
			IncludeHidden bool      `json:"include_hidden"`
			IncludeHeader bool      `json:"include_header_content"`
		} `json:"request"`
		Expected json.RawMessage `json:"expected"`
		Error    string          `json:"error"`
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if err = decoder.Decode(&cases); err != nil {
		t.Fatal(err)
	}
	for i, c := range cases {
		t.Run(strconv.Itoa(i)+"-"+c.Request.Mode, func(t *testing.T) {
			var actual any
			var err error
			r := c.Request
			switch r.Mode {
			case "parse":
				actual, err = Parse(r.HTML, r.Config, r.URL)
			case "flatten":
				actual, err = Flatten(r.HTML, r.IncludeHidden, r.IncludeHeader)
			case "walk":
				var fields Object
				var cursor int
				fields, cursor, err = WalkSteps(r.Elements, r.Steps, r.Start)
				actual = Object{"fields": fields, "cursor": cursor}
			case "classify-rendered":
				actual, err = ClassifyRendered(r.HTML, r.Config, *r.URL)
			default:
				t.Fatal("unknown fixture mode")
			}
			if c.Error != "" {
				if err == nil {
					t.Fatal("expected canonical rejection")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			// Compare wire values; Go's internal integer and map types are immaterial.
			encoded, err := json.Marshal(actual)
			if err != nil {
				t.Fatal(err)
			}
			var got, want any
			if err = json.Unmarshal(encoded, &got); err != nil {
				t.Fatal(err)
			}
			if err = json.Unmarshal(c.Expected, &want); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("actual=%s\nexpected=%s", encoded, c.Expected)
			}
		})
	}
}
