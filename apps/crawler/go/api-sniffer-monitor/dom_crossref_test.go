package apisniffer

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestDOMCrossrefMatchesOriginalPython(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_dom_crossref.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name     string
		BoardURL string `json:"board_url"`
		Items    json.RawMessage
		Links    []string
		URLs     []string
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 12 {
		t.Fatal("original cases unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			d, e := Decode(append(append([]byte(`{"jobs":`), c.Items...), '}'))
			if e != nil {
				t.Fatal(e)
			}
			items, e := d.items("jobs", false, false)
			if e != nil {
				t.Fatal(e)
			}
			field, urls, e := DOMCrossrefURLs(d, items, c.BoardURL, c.Links)
			if e != nil {
				t.Fatal(e)
			}
			got := []string{}
			for _, item := range items {
				if v, exists := item[field]; exists {
					id, e := d.String(v)
					if e != nil {
						t.Fatal(e)
					}
					if u := urls[id]; u != "" {
						got = append(got, u)
					}
				}
			}
			// Python's URL map deliberately retains the last duplicate ID. Compare
			// dictionary behavior separately from its intermediate positional list.
			want := c.URLs
			if c.Name == "duplicate-id-last-content" {
				want = []string{c.URLs[len(c.URLs)-1], c.URLs[len(c.URLs)-1]}
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatal("original cross-reference differs", got, want)
			}
		})
	}
}

func TestBrowserReplayDOMCrossrefPreservesRichAndExplicitURLOnlyInventories(t *testing.T) {
	for _, rich := range []bool{true, false} {
		name := "explicit-url-only"
		if rich {
			name = "rich-dom-crossref"
		}
		t.Run(name, func(t *testing.T) {
			fields := `,"url_field":"url"`
			body := `{"jobs":[{"requisitionId":3829,"title":"A","url":"https://example.com/ux/ats/requisition/3829"},{"requisitionId":3821,"title":"B","url":"https://example.com/ux/ats/requisition/3821"}],"total":2}`
			if rich {
				fields = `,"fields":{"title":"title"}`
				body = `{"jobs":[{"requisitionId":3829,"title":"A"},{"requisitionId":3821,"title":"B"}],"total":2}`
			}
			o, e := BrowserReplayOptionsFromMetadata("https://example.com/careers", `{"browser":true,"api_url":"https://example.com/api","json_path":"jobs"`+fields+`}`)
			if e != nil {
				t.Fatal(e)
			}
			doc, e := Decode([]byte(body))
			if e != nil {
				t.Fatal(e)
			}
			calls := 0
			inv, e := DiscoverBrowserReplay(context.Background(), o, func(context.Context, Request) (*Document, error) { return doc, nil }, PythonJoinURL, false, func(context.Context) ([]string, error) {
				calls++
				return []string{"https://example.com/ux/ats/requisition/3829"}, nil
			})
			if e != nil || rich && calls != 1 || !rich && calls != 0 || len(inv.Jobs) != 2 || inv.Truncated || inv.URLOnly == rich {
				t.Fatal("original complete inventory differs", e, inv, calls)
			}
			if inv.Jobs[0].URL != "https://example.com/ux/ats/requisition/3829" || inv.Jobs[1].URL != "https://example.com/ux/ats/requisition/3821" {
				t.Fatal("posting identities lost")
			}
		})
	}
}
