package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"strconv"
	"testing"
)

func TestMokahrIdentityBootstrapCryptoAndFieldsMatchActualPython(t *testing.T) {
	var corpus struct {
		Options []struct {
			Name, URL string
			Metadata  json.RawMessage
			Expected  []map[string]any
			Error     bool
		}
		Bootstrap []struct {
			Name, Page, IV string
			Cities         map[string]string
			Error          bool
		}
		Listing []struct {
			Name     string
			Envelope json.RawMessage
			Jobs     []map[string]any
			Total    int
			Error    bool
		}
		Projection []struct {
			Name          string
			Raw, Expected json.RawMessage
			Cities        map[string]string
		}
	}
	b, e := os.ReadFile("../ordinary-worker/testdata/python_mokahr.json")
	if e != nil || json.Unmarshal(b, &corpus) != nil {
		t.Fatal("actual Python corpus missing")
	}
	if len(corpus.Options) != 20 || len(corpus.Bootstrap) != 7 || len(corpus.Listing) != 10 || len(corpus.Projection) != 21 {
		t.Fatal("reference coverage changed")
	}
	partition := MokahrPartition{PageURL: "https://app.mokahr.com/social-recruitment/zte/47588", Origin: "https://app.mokahr.com", Path: "social-recruitment", OrgID: "zte", SiteID: 47588}
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
	for _, c := range corpus.Options {
		t.Run("options/"+c.Name, func(t *testing.T) {
			o, e := MokahrOptionsFromMetadata(c.URL, string(c.Metadata))
			if (e != nil) != c.Error {
				t.Fatalf("reference error=%v, native=%v", c.Error, e)
			}
			if e != nil {
				return
			}
			rows := []map[string]any{}
			for _, p := range o.Partitions {
				rows = append(rows, map[string]any{"page_url": p.PageURL, "origin": p.Origin, "path": p.Path, "org_id": p.OrgID, "site_id": p.SiteID})
				if !o.ResourceMatches(p.PageURL) || !o.ResourceMatches(p.APIURL()) || o.ResourceMatches(p.Origin+"/unrelated") {
					t.Fatal("resource authority differs")
				}
			}
			compare(t, rows, c.Expected)
		})
	}
	for _, c := range corpus.Bootstrap {
		t.Run("bootstrap/"+c.Name, func(t *testing.T) {
			iv, cities, e := MokahrBootstrap(c.Page, partition)
			if (e != nil) != c.Error {
				t.Fatalf("reference error=%v, native=%v", c.Error, e)
			}
			if e != nil {
				return
			}
			if iv != c.IV {
				t.Fatal("bootstrap differs")
			}
			compare(t, cities, c.Cities)
		})
	}
	for _, c := range corpus.Listing {
		t.Run("listing/"+c.Name, func(t *testing.T) {
			d, e := Decode(c.Envelope)
			if e != nil {
				t.Fatal(e)
			}
			jobs, total, e := MokahrListing(d, "0123456789abcdef", partition)
			if (e != nil) != c.Error {
				t.Fatalf("reference error=%v, native=%v", c.Error, e)
			}
			if e != nil {
				return
			}
			if total != c.Total {
				t.Fatal("authenticated total differs")
			}
			compare(t, jobs, c.Jobs)
		})
	}
	for _, c := range corpus.Projection {
		t.Run("projection/"+c.Name, func(t *testing.T) {
			d, e := Decode(c.Raw)
			if e != nil {
				t.Fatal(e)
			}
			cities := map[int]string{}
			for k, v := range c.Cities {
				n, e := strconv.Atoi(k)
				if e != nil {
					t.Fatal(e)
				}
				cities[n] = v
			}
			j, e := MokahrProject(d.Value.(map[string]any), partition, cities)
			if e != nil {
				t.Fatal(e)
			}
			var expected any
			if json.Unmarshal(c.Expected, &expected) != nil {
				t.Fatal("invalid reference")
			}
			compare(t, j, expected)
		})
	}
}
