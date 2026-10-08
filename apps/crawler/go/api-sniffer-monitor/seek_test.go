package apisniffer

import (
	"context"
	"encoding/json"
	"net/url"
	"os"
	"reflect"
	"sort"
	"strconv"
	"testing"
)

func TestSeekActualPythonIdentityPagesAndInventories(t *testing.T) {
	var cases []struct {
		Name, Kind, Source string
		Config             map[string]any
		Payload            json.RawMessage
		Page               int
		Pages              []json.RawMessage
		Output             json.RawMessage
		Error              bool
	}
	b, e := os.ReadFile("testdata/python_seek.json")
	if e != nil || json.Unmarshal(b, &cases) != nil || len(cases) < 40 {
		t.Fatal("actual Python SEEK reference missing", e)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			var actual any
			var err error
			switch c.Kind {
			case "identity":
				m, _ := json.Marshal(c.Config)
				o, e := SeekOptionsFromMetadata(c.Source, string(m))
				err = e
				if e == nil {
					actual = []string{o.Host, o.Advertiser}
					if !o.ResourceMatches(o.PageURL(1)) || !o.ResourceMatches(o.PageURL(500)) || o.ResourceMatches(o.PageURL(501)) || o.ResourceMatches(o.PageURL(1)+"&page=1") {
						t.Fatal("request scope", o)
					}
				}
			case "page":
				ids, total, e := ParseSeekPage(c.Payload, "9094357", c.Page)
				err = e
				if e == nil {
					actual = map[string]any{"ids": ids, "total": total}
				}
			case "inventory":
				o, e := SeekOptionsFromMetadata(c.Source, "{}")
				if e != nil {
					t.Fatal(e)
				}
				out, e := DiscoverSeek(context.Background(), o, func(ctx context.Context, resource string) ([]byte, error) {
					if !o.ResourceMatches(resource) {
						t.Fatal("foreign resource")
					}
					u, _ := url.Parse(resource)
					page, _ := strconv.Atoi(u.Query().Get("page"))
					return c.Pages[page-1], nil
				})
				err = e
				if e == nil {
					sort.Strings(out)
					actual = out
				} else if len(out) != 0 {
					t.Fatal("failed inventory exposed a prefix")
				}
			default:
				t.Fatal("unknown reference kind")
			}
			if (err != nil) != c.Error {
				t.Fatal(actual, err, c.Error)
			}
			if c.Error {
				return
			}
			b, _ := json.Marshal(actual)
			var got, want any
			if json.Unmarshal(b, &got) != nil || json.Unmarshal(c.Output, &want) != nil || !reflect.DeepEqual(got, want) {
				t.Fatal(string(b), string(c.Output))
			}
		})
	}
}
