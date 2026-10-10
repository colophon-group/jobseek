package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type remainingDetailCase struct {
	Provider, Mode, Source string
	BoardURL               string `json:"board_url"`
	Config                 json.RawMessage
	Row, Expected          map[string]any
	Error                  bool
	Exchanges              []struct {
		URL, Body   string
		Status      int
		ContentType string `json:"content_type"`
	}
}

func remainingDetailCases(t *testing.T) []remainingDetailCase {
	t.Helper()
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_remaining_http_detail_fields.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []remainingDetailCase
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 13 {
		t.Fatal("original detail corpus unavailable")
	}
	for i := range cases {
		c := &cases[i]
		c.BoardURL = "https://employer.example/careers"
		c.Source = c.BoardURL + "#/offer/23/job"
		c.Config = json.RawMessage(`{"company_key":"synthetic_company_key_12345","flow":"web","locale":"fr-CH"}`)
		if c.Provider == "headhunter" {
			c.BoardURL = "https://hh.ru/employer/42"
			c.Source = "https://hh.ru/vacancy/101"
			c.Config = json.RawMessage(`{"proxy":true}`)
		}
		o, e := api.RemainingHTTPDetailOptionsFromConfig(c.Provider, c.BoardURL, c.Source, string(c.Config))
		if e != nil {
			t.Fatal(e)
		}
		body, _ := json.Marshal(c.Row)
		c.Exchanges = append(c.Exchanges, struct {
			URL, Body   string
			Status      int
			ContentType string `json:"content_type"`
		}{o.Endpoint, string(body), 200, "application/json; charset=utf-8"})
	}
	if directory := os.Getenv("JOBSEEK_REMAINING_HTTP_PUBLIC_CAPTURE_DIR"); directory != "" {
		for _, slug := range []string{"city-of-montreux-careers", "fei-careers", "kraft-heinz-careers-ru", "sucden-russia-hh"} {
			tag := "1"
			if slug == "kraft-heinz-careers-ru" || slug == "sucden-russia-hh" {
				tag = "2"
			}
			raw, e := os.ReadFile(directory + "/native999-remaining-http-" + slug + "-public-detail-capture" + tag + "-2026-10-10.json")
			if e != nil {
				t.Fatal("private detail capture missing", slug)
			}
			var public []remainingDetailCase
			if json.Unmarshal(raw, &public) != nil {
				t.Fatal("private detail capture invalid", slug)
			}
			for i := range public {
				public[i].Mode = "public-" + slug + fmt.Sprint(i)
			}
			cases = append(cases, public...)
		}
	}
	return cases
}

func TestRemainingHTTPOriginalDetailsThroughVerifiedTransport(t *testing.T) {
	for _, c := range remainingDetailCases(t) {
		t.Run(c.Provider+"/"+c.Mode, func(t *testing.T) {
			o, e := api.RemainingHTTPDetailOptionsFromConfig(c.Provider, c.BoardURL, c.Source, string(c.Config))
			if e != nil {
				t.Fatal(e)
			}
			md := map[string]any{}
			json.Unmarshal(c.Config, &md)
			md["board_url"] = c.BoardURL
			p := queue.WorkdayDetailProfile{SourceURL: c.Source, Endpoint: o.Endpoint, Profile: c.Provider + ".api-detail/v1", HTTPAPIConfig: md}
			if o.Proxy {
				p.Profile = "headhunter.proxy-api-detail/v1"
			}
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if calls >= len(c.Exchanges) {
					t.Error("extra detail request")
					w.WriteHeader(400)
					return
				}
				x := c.Exchanges[calls]
				calls++
				want, _ := url.Parse(x.URL)
				if r.Method != "GET" || r.Host != want.Host || r.URL.Path != want.Path || !reflect.DeepEqual(r.URL.Query(), want.Query()) {
					t.Error("unbound detail request")
				}
				w.Header().Set("Content-Type", x.ContentType)
				w.WriteHeader(x.Status)
				fmt.Fprint(w, x.Body)
			}))
			if o.Proxy {
				client = credentialedProxyFixture(t, client)
			}
			fields, reserved, e := fetchRemainingHTTPDetail(context.Background(), client.client, p, func(context.Context, time.Duration) error { return nil })
			if c.Error {
				if e == nil || fields != nil {
					t.Fatal("original failed detail gained write authority")
				}
				return
			}
			if e != nil || reserved != nil {
				t.Fatal("original detail failed", e)
			}
			for key, value := range fields {
				if value == nil {
					delete(fields, key)
				}
			}
			got, _ := json.Marshal(fields)
			want, _ := json.Marshal(c.Expected)
			var a, b any
			json.Unmarshal(got, &a)
			json.Unmarshal(want, &b)
			if !reflect.DeepEqual(a, b) || calls != len(c.Exchanges) {
				keys := map[string]bool{}
				for key := range fields {
					keys[key] = true
				}
				for key := range c.Expected {
					keys[key] = true
				}
				for key := range keys {
					if !reflect.DeepEqual(a.(map[string]any)[key], b.(map[string]any)[key]) {
						t.Errorf("original field differs: %s", key)
					}
				}
				t.Fatal("original detail fields/request count changed", calls, len(c.Exchanges))
			}
		})
	}
}
