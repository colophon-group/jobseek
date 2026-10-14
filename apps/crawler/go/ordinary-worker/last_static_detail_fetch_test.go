package worker

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	q "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type lastStaticHTTPTrace struct {
	Provider, Name, Source string
	Config                 map[string]any
	Requests               []struct {
		Method, URL, Body, Location string
		Status                      int
		BodySize                    int `json:"body_size,omitempty"`
	}
	Waits   int
	Failed  bool
	Content map[string]any
}

func readLastStaticHTTP(t *testing.T) []lastStaticHTTPTrace {
	t.Helper()
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_last_static_http.json")
	var corpus struct{ Requests []lastStaticHTTPTrace }
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Requests) != 55 {
		t.Fatal("original HTTP corpus unavailable", err, len(corpus.Requests))
	}
	return corpus.Requests
}

func lastStaticProfile(t *testing.T, trace lastStaticHTTPTrace) q.WorkdayDetailProfile {
	t.Helper()
	o, err := api.StaticProviderDetailOptionsForSource(trace.Provider, trace.Source)
	if err != nil {
		t.Fatal(err)
	}
	if trace.Provider == "onlyfy" {
		language, _ := trace.Config["language"].(string)
		o.Endpoint, _, _, err = api.OnlyfyDetailURLs(trace.Source, language)
		if err != nil {
			t.Fatal(err)
		}
	}
	return q.WorkdayDetailProfile{Profile: trace.Provider + ".public-detail/v1", SourceURL: trace.Source, Endpoint: o.Endpoint, HTTPAPIConfig: trace.Config}
}

func TestLastStaticOriginalHTTPTracesAndFields(t *testing.T) {
	for _, trace := range readLastStaticHTTP(t) {
		t.Run(trace.Provider+"/"+trace.Name, func(t *testing.T) {
			calls, waits := 0, 0
			client := &http.Client{Transport: seekDetailTransport(func(r *http.Request) (*http.Response, error) {
				i := calls
				calls++
				if i >= len(trace.Requests) {
					t.Error("additional request")
					return nil, errors.New("unexpected request")
				}
				want := trace.Requests[i]
				if r.Method != want.Method || r.URL.String() != want.URL {
					t.Error("original request differs", r.Method, r.URL, want.Method, want.URL)
				}
				if want.Status == 0 {
					return nil, errors.New("fixture")
				}
				body := want.Body
				if want.BodySize != 0 {
					body = strings.Repeat("x", want.BodySize)
				}
				return &http.Response{StatusCode: want.Status, Header: http.Header{"Content-Type": {"text/html; charset=utf-8"}, "Location": {want.Location}}, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})}
			out, reserved, err := fetchLastStaticDetail(context.Background(), client, lastStaticProfile(t, trace), func(context.Context, time.Duration) error { waits++; return nil })
			if (err != nil) != trace.Failed || reserved != nil || calls != len(trace.Requests) || waits != trace.Waits {
				t.Fatal("original outcome differs", err, trace.Failed, calls, len(trace.Requests), waits, trace.Waits)
			}
			if err == nil {
				for field, want := range trace.Content {
					a, _ := json.Marshal(out[field])
					b, _ := json.Marshal(want)
					if string(a) != string(b) {
						t.Errorf("original field %s differs: %s / %s", field, a, b)
					}
				}
			}
		})
	}
}

func TestLastStaticPublisherPolicyBeforeRetryAndParse(t *testing.T) {
	for _, trace := range readLastStaticHTTP(t) {
		if trace.Name != "200" {
			continue
		}
		for _, status := range []int{200, 302, 503} {
			t.Run(trace.Provider+"/"+http.StatusText(status), func(t *testing.T) {
				calls := 0
				client := &http.Client{Transport: seekDetailTransport(func(r *http.Request) (*http.Response, error) {
					calls++
					return &http.Response{StatusCode: status, Header: http.Header{"Tdm-Reservation": {"1"}, "Location": {"https://foreign.example/job"}}, Body: io.NopCloser(strings.NewReader(trace.Requests[0].Body)), Request: r}, nil
				})}
				out, reserved, err := fetchLastStaticDetail(context.Background(), client, lastStaticProfile(t, trace), func(context.Context, time.Duration) error { t.Error("policy retried"); return nil })
				if err != nil || reserved == nil || out != nil || calls != 1 {
					t.Fatal("publisher policy lost", err, reserved, calls)
				}
			})
		}
	}
}
