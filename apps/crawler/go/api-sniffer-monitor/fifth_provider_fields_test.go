package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestFifthProviderFieldsMatchActualPython(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_fifth_provider_fields.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Provider, Name, Source, Culture, HTML string
		Row                                   map[string]any
		Expected                              map[string]any
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	if e = dec.Decode(&cases); e != nil || len(cases) != 39 {
		t.Fatal("actual Python corpus unavailable", e)
	}
	for _, c := range cases {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			var got map[string]any
			var e error
			switch c.Provider {
			case "adp":
				b, err := ADPBoardFromURL(c.Source)
				if err != nil {
					t.Fatal(err)
				}
				got = ADPJobFields(c.Row, b)
			case "cornerstone":
				b, err := CornerstoneBoardFromURL(c.Source)
				if err != nil {
					t.Fatal(err)
				}
				got = CornerstoneJobFields(c.Row, b, c.Culture)
			case "paylocity":
				o, err := PaylocityOptionsFromMetadata(c.Source, "{}")
				if err != nil {
					t.Fatal(err)
				}
				p, _ := json.Marshal(map[string]any{"Jobs": []any{c.Row}})
				rows, err := PaylocityPage("window.pageData="+string(p)+";", o)
				if err != nil {
					t.Fatal(err)
				}
				if len(rows) > 0 {
					got = rows[0]
				}
			case "paylocity-detail":
				got, e = ParsePaylocityDetail(c.HTML)
			default:
				t.Fatal("unexpected reference provider")
			}
			if e != nil {
				t.Fatal(e)
			}
			if c.Expected == nil {
				if got != nil {
					t.Fatal("invalid row became content")
				}
				return
			}
			keys := []string{"url", "title", "description", "locations", "employment_type", "job_location_type", "date_posted", "language", "base_salary", "extras", "metadata"}
			want := map[string]any{}
			out := map[string]any{}
			for _, k := range keys {
				if c.Provider == "paylocity-detail" && k == "url" {
					continue
				}
				want[k] = c.Expected[k]
				out[k] = got[k]
			}
			body, _ := json.Marshal(out)
			d, err := Decode(body)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(d.Value, want) {
				t.Fatalf("field contract changed:\nactual=%s\nexpected=%v", body, want)
			}
		})
	}
}
