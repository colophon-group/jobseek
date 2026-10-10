package apisniffer

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"testing"
)

func TestOriginalInforPairedDetailSessionRequestsAndFields(t *testing.T) {
	var cases []struct {
		Name, Board, Source string
		Error               bool
		Job                 map[string]any
		Exchanges           []struct {
			Method, URL, Body string
			Headers           map[string]string
			ResponseHeaders   map[string]string `json:"response_headers"`
		}
	}
	b, e := os.ReadFile("testdata/python_last_infor_detail.json")
	if e != nil || json.Unmarshal(b, &cases) != nil || len(cases) != 14 {
		t.Fatal("original detail corpus")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o, e := LastHTTPDetailOptionsFromConfig("infor", c.Board, c.Source, "{}")
			if e != nil {
				t.Fatal(e)
			}
			used := 0
			got, e := FetchInforDetail(context.Background(), o, func(_ context.Context, r Request) ([]byte, http.Header, error) {
				if used >= len(c.Exchanges) {
					t.Fatal("unexpected detail request")
				}
				x := c.Exchanges[used]
				used++
				u, _ := url.Parse(r.URL)
				v, _ := url.Parse(x.URL)
				if r.Method != x.Method || u.Scheme != v.Scheme || u.Host != v.Host || u.Path != v.Path || !reflect.DeepEqual(u.Query(), v.Query()) {
					t.Fatal("original paired session request changed")
				}
				if used > 1 {
					for k, want := range x.Headers {
						if r.Headers.Get(k) != want {
							t.Fatal("original session header changed", k)
						}
					}
				}
				h := http.Header{}
				for k, v := range x.ResponseHeaders {
					h.Set(k, v)
				}
				return []byte(x.Body), h, nil
			})
			if (e != nil) != c.Error || used != len(c.Exchanges) {
				t.Fatal("original paired detail outcome changed", e)
			}
			if c.Error {
				if got != nil {
					t.Fatal("failed detail retained content")
				}
				return
			}
			b, _ := json.Marshal(got)
			actual := map[string]any{}
			json.Unmarshal(b, &actual)
			for k, want := range c.Job {
				if !reflect.DeepEqual(actual[k], want) {
					t.Fatal("original paired detail field changed", k, actual[k], want)
				}
			}
		})
	}
}

func TestOriginalPeopleSoftPairedDetailFieldsAndAdapterInputs(t *testing.T) {
	var cases []struct {
		Name, Source string
		ExpectedID   string `json:"expected_id"`
		Error        bool
		Job          map[string]any
		Adapters     map[string]struct {
			Input string
			Value any
		}
	}
	b, e := os.ReadFile("testdata/python_last_peoplesoft_detail.json")
	if e != nil || json.Unmarshal(b, &cases) != nil || len(cases) != 15 {
		t.Fatal("original detail corpus")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			used := map[string]bool{}
			adapter := func(key, input string) any {
				x, ok := c.Adapters[key]
				if !ok || x.Input != input {
					t.Fatal("original detail adapter input changed", key)
				}
				used[key] = true
				return x.Value
			}
			got, e := ParsePeopleSoftDetail(c.Source, c.ExpectedID, func(s string) any { return adapter("employment", s) }, func(s string) any { return adapter("workplace", s) }, func(s string) (any, error) { return adapter("salary", s), nil })
			if (e != nil) != c.Error {
				t.Fatal("original paired detail outcome changed", e)
			}
			if len(used) != len(c.Adapters) {
				t.Fatal("original detail adapter calls changed")
			}
			if c.Error {
				if got != nil {
					t.Fatal("failed detail retained content")
				}
				return
			}
			b, _ := json.Marshal(got)
			actual := map[string]any{}
			json.Unmarshal(b, &actual)
			for k, want := range c.Job {
				if !reflect.DeepEqual(actual[k], want) {
					t.Fatal("original paired detail field changed", k, actual[k], want)
				}
			}
		})
	}
}
