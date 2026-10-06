package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestAlmaCareerHostsWidgetsGroupsAndFieldsMatchActualPython(t *testing.T) {
	var corpus struct {
		Host []struct {
			Name, URL string
			Metadata  json.RawMessage
			Expected  *string
		}
		Widget []struct {
			Name, Source string
			Inline       bool
			Expected     map[string]any
		}
		Chunks []struct {
			Name, Source string
			Expected     []string
		}
		Groups []struct {
			Name          string
			Raw, Expected json.RawMessage
		}
		Projection []struct {
			Name          string
			Raw, Expected json.RawMessage
		}
	}
	b, e := os.ReadFile("../ordinary-worker/testdata/python_almacareer.json")
	if e != nil || json.Unmarshal(b, &corpus) != nil {
		t.Fatal("actual Python corpus missing")
	}
	if len(corpus.Host) != 8 || len(corpus.Widget) != 13 || len(corpus.Chunks) != 3 || len(corpus.Groups) != 3 || len(corpus.Projection) != 29 {
		t.Fatal("reference coverage changed")
	}
	compare := func(t *testing.T, actual, expected any) {
		t.Helper()
		var a, b any
		ab, e := json.Marshal(actual)
		if e != nil {
			t.Fatal(e)
		}
		bb, e := json.Marshal(expected)
		if e != nil {
			t.Fatal(e)
		}
		if json.Unmarshal(ab, &a) != nil || json.Unmarshal(bb, &b) != nil || !reflect.DeepEqual(a, b) {
			t.Fatalf("actual parser result differs from frozen Python\nactual=%s\nexpected=%s", ab, bb)
		}
	}
	for _, c := range corpus.Host {
		t.Run("host/"+c.Name, func(t *testing.T) {
			o, e := AlmaOptionsFromMetadata(c.URL, string(c.Metadata))
			if c.Expected == nil {
				if e == nil {
					t.Fatal("unknown tenant admitted")
				}
				return
			}
			if e != nil || o.Host != *c.Expected {
				t.Fatalf("host differs: %v", e)
			}
			if !o.ResourceMatches(o.RootURL()) || !o.ResourceMatches(o.ScriptURL()) || !o.ResourceMatches(AlmaGraphQLURL) || o.ResourceMatches(o.RootURL()+"unrelated") {
				t.Fatal("resource authority differs")
			}
		})
	}
	for _, c := range corpus.Widget {
		t.Run("widget/"+c.Name, func(t *testing.T) {
			w, ok := ExtractAlmaWidget(c.Source, c.Inline)
			if ok != (c.Expected != nil) {
				t.Fatal("widget outcome differs")
			}
			if !ok {
				return
			}
			compare(t, map[string]any{"id": w.ID, "apiKey": w.APIKey, "detail_path": w.DetailPath}, c.Expected)
		})
	}
	for _, c := range corpus.Chunks {
		t.Run("chunks/"+c.Name, func(t *testing.T) { compare(t, AlmaReactChunks(c.Source), c.Expected) })
	}
	for _, c := range corpus.Groups {
		t.Run("groups/"+c.Name, func(t *testing.T) {
			d, e := Decode(c.Raw)
			if e != nil {
				t.Fatal(e)
			}
			rows, e := AlmaFlattenGroups(d.Value)
			if e != nil {
				t.Fatal(e)
			}
			var expected any
			if json.Unmarshal(c.Expected, &expected) != nil {
				t.Fatal("bad reference")
			}
			compare(t, rows, expected)
		})
	}
	for _, c := range corpus.Projection {
		t.Run("projection/"+c.Name, func(t *testing.T) {
			d, e := Decode(c.Raw)
			if e != nil {
				t.Fatal(e)
			}
			j, e := AlmaProject(d, d.Value.(map[string]any), "acme.jobs.cz", "detail-pozice", "cz")
			if e != nil {
				t.Fatal(e)
			}
			var expected any
			if json.Unmarshal(c.Expected, &expected) != nil {
				t.Fatal("bad reference")
			}
			compare(t, j, expected)
		})
	}
}
