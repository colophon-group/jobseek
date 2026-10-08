package apisniffer

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func avatureBoardProjection(b AvatureBoard) map[string]any {
	return map[string]any{"host": b.Host, "prefix": b.Prefix, "page": b.Page}
}
func TestAvatureActualPythonIdentityPagesAndInventories(t *testing.T) {
	var cases []struct {
		Name, Kind, Source, HTML string
		Config                   map[string]any
		Pages                    []string
		Output                   json.RawMessage
		Error                    bool
	}
	b, e := os.ReadFile("testdata/python_avature.json")
	if e != nil || json.Unmarshal(b, &cases) != nil || len(cases) < 35 {
		t.Fatal("actual Python Avature reference missing", e)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			var actual any
			failed := false
			switch c.Kind {
			case "identity":
				b, ok := AvatureBoardFromURL(c.Source, true)
				failed = !ok
				if ok {
					actual = avatureBoardProjection(b)
				}
			case "options":
				m, _ := json.Marshal(c.Config)
				o, e := AvatureOptionsFromMetadata(c.Source, string(m))
				failed = e != nil
				if e == nil {
					var portal any
					if o.PortalID != "" {
						portal = o.PortalID
					}
					actual = map[string]any{"board": avatureBoardProjection(o.Board), "configured": o.Configured, "portal_id": portal}
				}
			case "page":
				p, e := ParseAvaturePage([]byte(c.HTML), c.Source)
				failed = e != nil
				if e == nil {
					var total, start, end any
					if p.HasTotal {
						total = p.Total
					}
					if p.HasRange {
						start, end = p.Start, p.End
					}
					actual = map[string]any{"board": avatureBoardProjection(p.Board), "portal_id": p.PortalID, "total": total, "total_exact": p.Exact, "range_start": start, "range_end": end, "jobs": p.Jobs, "next_urls": p.Next}
				}
			case "pagination":
				u, offset, ok := AvaturePaginationURL(c.Source, AvatureBoard{"acme.avature.net", "/careers", "SearchJobs"}, false)
				failed = !ok
				if ok {
					actual = []any{u, offset}
				}
			case "inventory":
				o, e := AvatureOptionsFromMetadata(c.Source, "{}")
				if e != nil {
					t.Fatal(e)
				}
				calls := 0
				out, e := DiscoverAvature(context.Background(), o, func(ctx context.Context, u string) ([]byte, error) {
					if !o.ResourceMatches(u) || calls >= len(c.Pages) {
						t.Fatal("foreign or unexpected resource", u)
					}
					v := c.Pages[calls]
					calls++
					return []byte(v), nil
				})
				failed = e != nil
				if e == nil {
					actual = map[string]any{"urls": out.URLs, "truncated": out.Truncated, "metadata": out.Metadata}
				} else if len(out.URLs) > 0 {
					t.Fatal("failed inventory exposed a prefix")
				}
			default:
				t.Fatal("unknown case")
			}
			if failed != c.Error {
				t.Fatal(actual, failed, c.Error)
			}
			if c.Error {
				return
			}
			encoded, _ := json.Marshal(actual)
			var got, want any
			if json.Unmarshal(encoded, &got) != nil || json.Unmarshal(c.Output, &want) != nil || !reflect.DeepEqual(got, want) {
				t.Fatal(string(encoded), string(c.Output))
			}
		})
	}
}
