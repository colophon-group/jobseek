package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestOriginalDarwinboxByteDanceFieldsIdentitiesRequests(t *testing.T) {
	var corpus struct {
		Fields []struct {
			Provider, Portal, Name string
			Row, Expected          json.RawMessage
		}
		Identities []struct {
			URL      string
			Expected json.RawMessage
		}
		Requests []struct {
			Provider, Portal string
			Body             map[string]any
			Headers          map[string]string
		}
		Normalizations map[string]*string
	}
	body, e := os.ReadFile("testdata/python_darwinbox_bytedance.json")
	if e != nil {
		t.Fatal(e)
	}
	if e = json.Unmarshal(body, &corpus); e != nil {
		t.Fatal(e)
	}
	normalize := func(s string) (*string, error) {
		v, ok := corpus.Normalizations[s]
		if !ok {
			return nil, errors.New("unqualified original HTML")
		}
		return v, nil
	}
	portal := func(kind string) ByteDancePortal {
		raw := "https://joinbytedance.com/search"
		if kind == "society" {
			raw = "https://jobs.bytedance.com/experienced/position"
		}
		if kind == "campus" {
			raw = "https://jobs.bytedance.com/campus/position"
		}
		p, e := ByteDanceOptionsFromURL(raw)
		if e != nil {
			t.Fatal(e)
		}
		return p
	}
	for _, c := range corpus.Fields {
		t.Run(c.Provider+"/"+c.Portal+"/"+c.Name, func(t *testing.T) {
			d, e := Decode(c.Row)
			if e != nil {
				t.Fatal(e)
			}
			var j Job
			valid := true
			if c.Provider == "darwinbox" {
				j, valid, e = DarwinboxJob(d.Value, DarwinboxBoard{"airtel.darwinbox.in", "main"}, normalize)
			} else {
				j, e = ByteDanceJob(d.Value.(map[string]any), portal(c.Portal))
			}
			if e != nil {
				t.Fatal(e)
			}
			if string(c.Expected) == "null" {
				if valid {
					t.Fatal("invalid job admitted")
				}
				return
			}
			if !valid {
				t.Fatal("original valid job refused")
			}
			b, _ := json.Marshal(j)
			var actual, expected map[string]any
			json.Unmarshal(b, &actual)
			json.Unmarshal(c.Expected, &expected)
			for k, v := range actual {
				if v == nil {
					delete(actual, k)
				}
			}
			if !reflect.DeepEqual(actual, expected) {
				t.Fatalf("fields differ\nactual%s\nexpected%s", b, c.Expected)
			}
		})
	}
	for _, c := range corpus.Identities {
		b, e := DarwinboxBoardFromURL(c.URL)
		if string(c.Expected) == "null" {
			if e == nil {
				t.Fatal("foreign identity", c.URL)
			}
			continue
		}
		if e != nil {
			t.Fatal(c.URL, e)
		}
		var want map[string]string
		json.Unmarshal(c.Expected, &want)
		if b.Host != want["host"] || b.CompanyID != want["company_id"] {
			t.Fatal("portal identity changed")
		}
	}
	for _, c := range corpus.Requests {
		var r Request
		if c.Provider == "darwinbox" {
			r = DarwinboxBoard{"airtel.darwinbox.in", "main"}.PageRequest(2)
		} else {
			r = portal(c.Portal).PageRequest(2000, []string{"rd"})
		}
		var actual map[string]any
		json.Unmarshal([]byte(r.Body), &actual)
		if !reflect.DeepEqual(actual, c.Body) {
			t.Fatal("original request body changed", actual, c.Body)
		}
		for k, v := range c.Headers {
			if r.Headers.Get(k) != v {
				t.Fatal("original header changed", k)
			}
		}
	}
}
func TestDarwinboxStreamingPrefixAndIncompleteInventory(t *testing.T) {
	board := DarwinboxBoard{"airtel.darwinbox.in", "main"}
	normalize := func(s string) (*string, error) { return &s, nil }
	for _, mode := range []string{"complete", "changed_total", "duplicate", "invalid", "premature_partial", "later_failure", "premature_empty", "zero"} {
		t.Run(mode, func(t *testing.T) {
			calls, emitted := 0, 0
			out, e := DiscoverDarwinbox(context.Background(), board, func(_ context.Context, r Request) (*Document, error) {
				calls++
				var q map[string]any
				json.Unmarshal([]byte(r.Body), &q)
				page := int(q["page"].(float64))
				if mode == "later_failure" && page == 2 {
					return nil, errors.New("later failure")
				}
				n, total := 100, 101
				if page == 2 {
					n = 1
				}
				if mode == "zero" {
					n, total = 0, 0
				}
				if mode == "premature_partial" {
					n = 99
				}
				if mode == "premature_empty" && page == 2 {
					n = 0
				}
				if mode == "changed_total" && page == 2 {
					total = 100
				}
				rows := []any{}
				for i := 0; i < n; i++ {
					id := fmt.Sprint((page-1)*100 + i + 1)
					if mode == "duplicate" && page == 2 {
						id = "1"
					}
					title := "Role"
					if mode == "invalid" && page == 2 {
						title = ""
					}
					rows = append(rows, map[string]any{"id": id, "title": title, "jd": "<p>Build</p>"})
				}
				b, _ := json.Marshal(map[string]any{"status": "success", "job_counts": total, "data": rows})
				return Decode(b)
			}, normalize, func(jobs []Job) error { emitted += len(jobs); return nil })
			failure := strings.HasPrefix(mode, "premature_") || mode == "later_failure"
			if (e != nil) != failure {
				t.Fatal("completion changed", e)
			}
			if failure {
				want := 100
				if mode == "premature_partial" {
					want = 0
				}
				if len(out.Jobs) != want || emitted != want {
					t.Fatal("validated prefix changed", len(out.Jobs), emitted)
				}
				return
			}
			truncated := mode == "changed_total" || mode == "duplicate" || mode == "invalid"
			if out.Truncated != truncated {
				t.Fatal("absence authority changed")
			}
			if mode == "zero" && len(out.Jobs) != 0 {
				t.Fatal("zero inventory")
			}
		})
	}
}
func TestByteDancePartitionsPaginationCompletenessAndBinding(t *testing.T) {
	p, e := ByteDanceOptionsFromURL("https://jobs.bytedance.com/experienced/position")
	if e != nil {
		t.Fatal(e)
	}
	for _, bad := range []string{"http://jobs.bytedance.com/experienced/position", "https://jobs.bytedance.com/experienced/position?category=x", "https://evil.test/search"} {
		if _, e := ByteDanceOptionsFromURL(bad); e == nil {
			t.Fatal("foreign/filtered board")
		}
	}
	if p.RequestMatches(Request{Method: "GET", URL: p.Endpoint}) || p.RequestMatches(Request{Method: "POST", URL: ByteDanceFilterURL}) || p.RequestMatches(Request{Method: "GET", URL: ByteDanceFilterURL + "?evil=1"}) {
		t.Fatal("resource scope widened")
	}
	for _, mode := range []string{"complete", "overlap", "cap", "changed_total", "premature_empty", "missing_field", "zero", "later_failure"} {
		t.Run(mode, func(t *testing.T) {
			calls := 0
			out, e := DiscoverByteDance(context.Background(), p, func(_ context.Context, r Request) (*Document, error) {
				calls++
				if !p.RequestMatches(r) {
					t.Fatal("foreign request")
				}
				if r.Method == "GET" {
					count := 1001
					if mode == "zero" {
						count = 0
					}
					b, _ := json.Marshal(map[string]any{"code": 0, "data": map[string]any{"job_type_list": []any{map[string]any{"id": "a"}, map[string]any{"id": "b"}}, "job_type_count_map": map[string]int{"a": count, "b": count}}})
					return Decode(b)
				}
				var q map[string]any
				json.Unmarshal([]byte(r.Body), &q)
				offset := int(q["offset"].(float64))
				cat := q["job_category_id_list"].([]any)[0].(string)
				if mode == "later_failure" && offset > 0 {
					return nil, errors.New("later failure")
				}
				total := 1001
				if mode == "cap" {
					total = 10000
				}
				if mode == "changed_total" && offset > 0 {
					total = 1002
				}
				n := 1000
				if offset > 0 {
					n = 1
				}
				if mode == "premature_empty" && offset > 0 {
					n = 0
				}
				rows := []any{}
				for i := 0; i < n; i++ {
					id := cat + fmt.Sprint(offset+i+1)
					if mode == "overlap" {
						id = fmt.Sprint(offset + i + 1)
					}
					row := map[string]any{"id": id, "title": "Role", "description": "Build", "city_info": map[string]string{"en_name": "Zurich"}}
					if mode == "missing_field" {
						delete(row, "description")
					}
					rows = append(rows, row)
				}
				b, _ := json.Marshal(map[string]any{"code": 0, "data": map[string]any{"count": total, "job_post_list": rows}})
				return Decode(b)
			})
			valid := mode == "complete" || mode == "zero"
			if (e == nil) != valid {
				t.Fatal("partition completeness", e)
			}
			if !valid && len(out.Jobs) > 0 {
				t.Fatal("partial inventory published")
			}
			if mode == "complete" && len(out.Jobs) != 2002 {
				t.Fatal("lost partition")
			}
			if mode == "zero" && (len(out.Jobs) != 0 || calls != 1) {
				t.Fatal("zero filter authority")
			}
		})
	}
}
