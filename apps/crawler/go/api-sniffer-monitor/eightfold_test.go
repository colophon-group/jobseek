package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
	"time"
)

func TestEightfoldFieldsRoutesAndWatermarksMatchActualPython(t *testing.T) {
	type projection struct {
		Name, URL string
		Raw       json.RawMessage
		Expected  map[string]any
	}
	var corpus struct {
		PCSX, Detail []projection
		Watermark    []struct {
			Name, Now string
			Metadata  json.RawMessage
			Patch     map[string]any
			NeedsFull bool `json:"needs_full"`
			Error     bool
		}
	}
	body, e := os.ReadFile("../ordinary-worker/testdata/python_eightfold.json")
	if e != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.PCSX) != 18 || len(corpus.Detail) != 16 || len(corpus.Watermark) != 12 {
		t.Fatal("actual Python Eightfold corpus missing")
	}
	compare := func(t *testing.T, got any, expected any) {
		t.Helper()
		b, e := json.Marshal(got)
		if e != nil {
			t.Fatal(e)
		}
		var value any
		if json.Unmarshal(b, &value) != nil || !reflect.DeepEqual(value, expected) {
			t.Fatalf("actual reference differs: native=%v expected=%v", value, expected)
		}
	}
	for _, c := range corpus.PCSX {
		t.Run("pcsx/"+c.Name, func(t *testing.T) {
			d, e := Decode(c.Raw)
			if e != nil {
				t.Fatal(e)
			}
			compare(t, EightfoldPCSXFields(d.Value.(map[string]any), c.URL), c.Expected)
		})
	}
	for _, c := range corpus.Detail {
		t.Run("detail/"+c.Name, func(t *testing.T) {
			d, e := Decode(c.Raw)
			if e != nil {
				t.Fatal(e)
			}
			got, e := d.EightfoldDetailFields(d.Value.(map[string]any))
			if e != nil {
				t.Fatal(e)
			}
			compare(t, got, c.Expected)
		})
	}
	for _, c := range corpus.Watermark {
		t.Run("watermark/"+c.Name, func(t *testing.T) {
			d, e := Decode(c.Metadata)
			if e != nil {
				t.Fatal(e)
			}
			got, e := EightfoldReadWatermark(d.Value.(map[string]any))
			if (e != nil) != c.Error {
				t.Fatal("watermark failure differs", e)
			}
			if e != nil {
				return
			}
			compare(t, got.Patch(), c.Patch)
			now, e := time.Parse(time.RFC3339Nano, c.Now)
			if e != nil || got.NeedsFull(now) != c.NeedsFull {
				t.Fatal("full-crawl cadence differs", e)
			}
		})
	}
	var routes struct {
		Routes []struct {
			URL        string
			ID, Domain *string
			PCSXDomain *string `json:"pcsx_domain"`
		}
	}
	if json.Unmarshal(body, &routes) != nil || len(routes.Routes) != 6 {
		t.Fatal("route reference missing")
	}
	for _, c := range routes.Routes {
		t.Run("route/"+c.URL, func(t *testing.T) {
			empty := func(v *string) string {
				if v == nil {
					return ""
				}
				return *v
			}
			if EightfoldJobID(c.URL) != empty(c.ID) || EightfoldDomain(c.URL, false) != empty(c.Domain) || EightfoldDomain(c.URL, true) != empty(c.PCSXDomain) {
				t.Fatal("provider route differs")
			}
		})
	}
}
