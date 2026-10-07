package apisniffer

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

func TestBrowserReplayTraversalUsesOneFirstPageAndRealBrowserBudget(t *testing.T) {
	for _, c := range []struct {
		name        string
		http        bool
		total, want int
		truncated   bool
	}{
		{"browser-default", false, 0, 50, false},
		{"http-default", true, 0, 200, false},
		{"known-total-expansion", false, 75, 75, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			o, err := BrowserReplayOptionsFromMetadata("https://example.com/careers", `{"browser":true,"api_url":"https://example.com/api","json_path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"title"},"pagination":{"param_name":"page","style":"page","start_value":1,"increment":1}}`)
			if err != nil {
				t.Fatal(err)
			}
			calls := 0
			inventory, err := DiscoverBrowserReplay(context.Background(), o, func(_ context.Context, r Request) (*Document, error) {
				calls++
				if calls == 1 && r.URL != o.Inventory.Endpoint {
					t.Fatal("initial request changed")
				}
				total := ""
				if c.total > 0 {
					total = `"total":` + strconv.Itoa(c.total) + `,`
				}
				return Decode([]byte(`{` + total + `"jobs":[{"id":"` + strconv.Itoa(calls) + `"}]}`))
			}, func(base, path string) (string, error) { return path, nil }, c.http)
			if err != nil || calls != c.want || len(inventory.Jobs) != c.want || inventory.Truncated != c.truncated {
				t.Fatal("browser traversal budget differs", err, calls, len(inventory.Jobs), inventory.Truncated)
			}
			if o.Inventory.Pagination.MaxPages != 200 {
				t.Fatal("shared inventory options were mutated")
			}
		})
	}
}

func TestBrowserReplayPreservesInventoryAndNavigationControls(t *testing.T) {
	raw := `{"browser":true,"api_url":"https://example.com/api","method":"POST","json_path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"title"},"post_data":{"z":1,"a":2},"wait":"networkidle","timeout":12000,"settle":0.25}`
	o, e := BrowserReplayOptionsFromMetadata("https://example.com/careers", raw)
	if e != nil || o.Wait != "networkidle" || o.TimeoutMS != 12000 || o.SettleMS != 250 || o.Inventory.Body != `{"z":1,"a":2}` {
		t.Fatal("navigation/inventory differs", e, o.Inventory.Body)
	}
	for _, extra := range []string{`"actions":[]`, `"api_url_match":"/token/"`, `"persistent_context":false`, `"channel":"chrome"`, `"proxy":true`, `"render":true`, `"settle":-1`, `"timeout":0`} {
		if _, e := BrowserReplayOptionsFromMetadata("https://example.com/careers", strings.TrimSuffix(raw, "}")+","+extra+"}"); e == nil {
			t.Fatal("unsupported browser control admitted", extra)
		}
	}
}

func TestBrowserReplayCaptureSelectionMatchesActualPython(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_browser_replay_selection.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name, Path string
		Exchanges  []struct {
			URL, Method string
			Headers     map[string]string `json:"request_headers"`
			Body        json.RawMessage
		}
		Expected struct {
			Matched bool
			Headers map[string]string
			Body    json.RawMessage
		}
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 10 {
		t.Fatal("actual Python selector corpus unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			exchanges := []BrowserReplayExchange{}
			for _, row := range c.Exchanges {
				headers := http.Header{}
				for key, value := range row.Headers {
					headers.Set(key, value)
				}
				e, err := NewBrowserReplayExchange(row.URL, row.Method, headers, row.Body)
				if err != nil {
					t.Fatal(err)
				}
				exchanges = append(exchanges, e)
			}
			o := BrowserReplayOptions{Inventory: Options{Endpoint: "https://example.com/api?stored=1", Method: "POST", Path: c.Path}}
			headers, d, matched, e := SelectBrowserReplayExchange(o, exchanges)
			if e != nil || matched != c.Expected.Matched {
				t.Fatal("Python match differs", matched, e)
			}
			for key, value := range c.Expected.Headers {
				if headers.Get(key) != value {
					t.Fatal("Python header refresh differs")
				}
			}
			if string(c.Expected.Body) == "null" {
				if d != nil {
					t.Fatal("non-listing capture became authoritative data")
				}
				return
			}
			want, e := Decode(c.Expected.Body)
			if e != nil || d == nil || !reflect.DeepEqual(d.Value, want.Value) {
				t.Fatal("Python selected response differs", e)
			}
		})
	}
}

func TestBrowserReplayRanksMatchingCaptureAndKeepsCredentialsPrivate(t *testing.T) {
	o := BrowserReplayOptions{Inventory: Options{Endpoint: "https://example.com/api?old=1", Method: "POST", Path: "jobs"}}
	exchange := func(source, method, header, body string) BrowserReplayExchange {
		e, err := NewBrowserReplayExchange(source, method, http.Header{"Authorization": {header}}, []byte(body))
		if err != nil {
			t.Fatal(err)
		}
		return e
	}
	a := exchange("https://example.com/api?fresh=1", "POST", "one", `{"jobs":[{},1]}`)
	b := exchange("https://example.com/api?fresh=2", "POST", "two", `{"jobs":[{},{}]}`)
	tie := exchange("https://example.com/api?fresh=3", "POST", "tie", `{"jobs":[{},{}]}`)
	foreign := exchange("https://other.com/api", "POST", "foreign", `{"jobs":[{},{},{}]}`)
	wrong := exchange("https://example.com/api", "GET", "wrong", `{"jobs":[{},{},{}]}`)
	headers, document, matched, e := SelectBrowserReplayExchange(o, []BrowserReplayExchange{a, foreign, b, wrong, tie})
	if e != nil || !matched || headers.Get("Authorization") != "two" || document != b.document {
		t.Fatal("capture ranking differs", e)
	}
	headers.Set("Authorization", "modified")
	if b.headers.Get("Authorization") != "two" {
		t.Fatal("returned headers changed lease")
	}
	zero := exchange("https://example.com/api", "POST", "zero", `{"other":[]}`)
	headers, document, matched, e = SelectBrowserReplayExchange(o, []BrowserReplayExchange{zero})
	if e != nil || !matched || document != nil || headers.Get("Authorization") != "zero" {
		t.Fatal("zero-score capture lost header refresh", e)
	}
	if _, e := json.Marshal(b); e == nil || fmt.Sprintf("%+v %#v", b, b) != "private browser replay exchange private browser replay exchange" {
		t.Fatal("capture material escaped diagnostics")
	}
}

func TestBrowserReplayPaginationCapsMatchPython(t *testing.T) {
	base := `{"browser":true,"api_url":"https://example.com/api","json_path":"jobs","total_path":"total","url_template":"https://example.com/jobs/{id}","fields":{"title":"title"},"pagination":{"param_name":"page","start_value":1%s}}`
	for _, c := range []struct {
		extra, body string
		fallback    bool
		want        int
	}{
		{"", `{"jobs":[{}]}`, false, 50},
		{"", `{"jobs":[{}]}`, true, 200},
		{",\"max_pages\":5", `{"jobs":[{}]}`, false, 5},
		{",\"max_pages\":5", `{"jobs":[{},{}],"total":24}`, false, 12},
		{"", `{"jobs":[{}],"total":1000}`, false, 200},
		{",\"max_pages\":300", `{"jobs":[{}],"total":1000}`, false, 300},
		{"", `{"jobs":[],"total":1000}`, false, 50},
	} {
		o, e := BrowserReplayOptionsFromMetadata("https://example.com/careers", fmt.Sprintf(base, c.extra))
		if e != nil {
			t.Fatal(e)
		}
		d, e := Decode([]byte(c.body))
		if e != nil {
			t.Fatal(e)
		}
		got, e := o.PaginationLimit(d, c.fallback)
		if e != nil || got != c.want {
			t.Fatal("Python browser/HTTP cap differs", got, c.want, e)
		}
	}
}
