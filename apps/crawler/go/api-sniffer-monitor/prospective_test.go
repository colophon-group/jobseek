package apisniffer

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"reflect"
	"strconv"
	"sync"
	"testing"

	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
)

func TestProspectiveOriginalInventories(t *testing.T) {
	body, e := os.ReadFile("testdata/python_prospective.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name, Board string
		Metadata    json.RawMessage
		Requests    []struct {
			URL, Method, Body string
			Headers           map[string]string
		}
		Responses []struct {
			Body    string
			Status  int
			Headers map[string]string
		}
		Jobs  []map[string]any
		Error bool
	}
	if e = json.Unmarshal(body, &cases); e != nil {
		t.Fatal(e)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o, e := ProspectiveOptionsFromMetadata(c.Board, string(c.Metadata))
			if e != nil {
				t.Fatal(e)
			}
			var mu sync.Mutex
			used := make([]bool, len(c.Requests))
			fetch := func(ctx context.Context, r Request) ([]byte, http.Header, error) {
				mu.Lock()
				defer mu.Unlock()
				if !o.ResourceMatches(r.URL) {
					t.Fatalf("resource escaped %s", r.URL)
				}
				found := -1
				for i, w := range c.Requests {
					if !used[i] && r.Method == w.Method && r.URL == w.URL && r.Body == w.Body {
						found = i
						break
					}
				}
				if found < 0 {
					t.Fatalf("unexpected request %s %s %s", r.Method, r.URL, r.Body)
				}
				w := c.Requests[found]
				for k, v := range w.Headers {
					if r.Headers.Get(k) != v {
						t.Fatalf("header %s differs: %s vs %s", k, r.Headers.Get(k), v)
					}
				}
				used[found] = true
				response := c.Responses[found]
				h := http.Header{}
				for k, v := range response.Headers {
					h.Set(k, v)
				}
				h.Set("X-Jobseek-Observed-Status", strconv.Itoa(response.Status))
				return []byte(response.Body), h, nil
			}
			jobs, e := DiscoverProspective(context.Background(), o, fetch, func(raw string, body []byte) (map[string]any, error) {
				return jsonld.Parse(raw, body, map[string]any{})
			})
			if (e != nil) != c.Error {
				t.Fatalf("error parity: %v", e)
			}
			for i, u := range used {
				if !u {
					t.Fatalf("original request %d absent", i)
				}
			}
			if c.Error {
				return
			}
			if len(jobs) != len(c.Jobs) {
				t.Fatalf("job count differs %d/%d", len(jobs), len(c.Jobs))
			}
			for i, j := range jobs {
				data, _ := json.Marshal(j)
				var actual map[string]any
				json.Unmarshal(data, &actual)
				for k, v := range c.Jobs[i] {
					if !reflect.DeepEqual(actual[k], v) {
						t.Fatalf("field %s differs: %v vs %v", k, actual[k], v)
					}
				}
			}
		})
	}
}
