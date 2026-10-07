package dom

import (
	"context"
	"encoding/json"
	"net/url"
	"os"
	"reflect"
	"testing"
)

func TestRichRowsMatchActualPythonInventory(t *testing.T) {
	var corpus struct {
		Cases []struct {
			Name, Source, Include string
			BaseURL               string `json:"base_url"`
			Options               json.RawMessage
			AllowEmpty            bool `json:"allow_empty"`
			Error                 bool
			Jobs                  []map[string]any
		}
	}
	raw, e := os.ReadFile("testdata/python_rich_rows.json")
	if e != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Cases) != 52 {
		t.Fatal("actual Python rich-row evidence missing", e)
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			config, e := RichRowsOptions(c.Options)
			var rows []RichRowJob
			if e == nil {
				include, e2 := CompileURLPattern(c.Include)
				if e2 != nil {
					t.Fatal(e2)
				}
				if c.Include == "" {
					include = nil
				}
				rows, e = ParseRichRows(context.Background(), c.Source, c.BaseURL, config, RichRowsPolicy{Include: include, AllowEmpty: c.AllowEmpty, JoinURL: func(base, href string) (string, error) {
					b, e := url.Parse(base)
					if e != nil {
						return "", e
					}
					h, e := url.Parse(href)
					if e != nil {
						return "", e
					}
					return b.ResolveReference(h).String(), nil
				}})
			}
			if (e != nil) != c.Error {
				t.Fatalf("Python terminal differs: error=%v want_error=%v", e, c.Error)
			}
			if e != nil {
				if len(rows) != 0 {
					t.Fatal("partial inventory returned after failure")
				}
				return
			}
			if len(rows) != len(c.Jobs) {
				t.Fatal("row count differs", len(rows), len(c.Jobs))
			}
			for i, row := range rows {
				b, _ := json.Marshal(row)
				var actual map[string]any
				_ = json.Unmarshal(b, &actual)
				for k, v := range actual {
					if !reflect.DeepEqual(v, c.Jobs[i][k]) {
						t.Errorf("Python %s differs: actual=%v expected=%v", k, v, c.Jobs[i][k])
					}
				}
			}
		})
	}
}

func TestRichRowsSelectorTranslationPreservesQuotedValuesAndRefusesComplexRelations(t *testing.T) {
	for _, c := range []struct{ input, expected string }{
		{"article:has(> a[href^='/jobs/'])", "article:haschild(a[href^='/jobs/'])"},
		{`article[data-text=":has(> a)"]`, `article[data-text=":has(> a)"]`},
	} {
		v, e := richRowsCSS(c.input)
		if e != nil || v != c.expected {
			t.Fatal("selector normalization differs", v, e)
		}
	}
	if _, e := richRowsCSS("article:has(> div > a)"); e == nil {
		t.Fatal("unproved relative path admitted")
	}
}
