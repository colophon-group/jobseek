package apisniffer

import (
	"encoding/json"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"os"
	"reflect"
	"testing"
)

func TestInlineDocumentCursorSectionsAndIdentitiesMatchActualPython(t *testing.T) {
	body, err := os.ReadFile("testdata/python_inline_document.json")
	var corpus struct {
		Cases []struct {
			Name, HTML               string
			Steps                    []dom.Object
			Item, Start, End         dom.Object
			Hidden, Error, Truncated bool
			Rows                     []dom.Object
		}
		Identities []struct {
			BoardURL             string `json:"board_url"`
			Titles, Stable, URLs []string
			Error                bool
		}
		Direct []struct{ Identity, URL string }
	}
	if err != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) != 12 || len(corpus.Identities) != 5 || len(corpus.Direct) != 4 {
		t.Fatal("actual Inline document corpus missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			rows, truncated, err := ExtractInlineRows(c.HTML, c.Steps, c.Item, c.Start, c.End, c.Hidden)
			if (err != nil) != c.Error || err == nil && (truncated != c.Truncated || !reflect.DeepEqual(rows, c.Rows)) {
				t.Fatal("inline cursor/section contract differs", err)
			}
		})
	}
	for _, c := range corpus.Identities {
		seen := map[string]int{}
		urls := []string{}
		var failure error
		for i, title := range c.Titles {
			var stable *string
			if c.Stable != nil {
				stable = &c.Stable[i]
			}
			url, err := InlineSyntheticURL(c.BoardURL, title, seen, stable)
			if err != nil {
				failure = err
				break
			}
			urls = append(urls, url)
		}
		if (failure != nil) != c.Error || !reflect.DeepEqual(urls, c.URLs) {
			t.Fatalf("inline identity differs: error=%v urls=%v expected=%v", failure, urls, c.URLs)
		}
	}
	for _, c := range corpus.Direct {
		url, err := InlineIdentityURL("https://example.com/jobs?lang=en#roles", c.Identity)
		if err != nil || url != c.URL {
			t.Fatal("provider identity escaping differs", err)
		}
	}
}
