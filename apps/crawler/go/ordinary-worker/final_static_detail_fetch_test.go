package worker

import (
	"context"
	"encoding/json"
	"errors"
	q "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"
)

type finalDetailCorpus struct {
	Cases []struct {
		Provider, Name, Body string
		Content              map[string]any
	}
	Requests []struct {
		Provider, Name, Body, Source string
		Statuses                     []int
		Requests                     []struct{ Method, URL string }
		Content                      map[string]any
		Failed                       bool
	}
	Sources map[string]string
}

func readFinalDetailCorpus(t *testing.T) finalDetailCorpus {
	t.Helper()
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_final_static_detail.json")
	var c finalDetailCorpus
	if e != nil || json.Unmarshal(raw, &c) != nil || len(c.Requests) != 33 || len(c.Cases) != 40 {
		t.Fatal("original corpus unavailable", e)
	}
	return c
}
func finalDetailProfile(provider, source string) q.WorkdayDetailProfile {
	return q.WorkdayDetailProfile{Profile: provider + ".public-detail/v1", SourceURL: source, Endpoint: source}
}
func TestFinalStaticOriginalRequestsAndFields(t *testing.T) {
	for _, c := range readFinalDetailCorpus(t).Requests {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			calls, waits := 0, 0
			client := &http.Client{Transport: seekDetailTransport(func(r *http.Request) (*http.Response, error) {
				i := calls
				calls++
				if i >= len(c.Requests) || r.Method != c.Requests[i].Method || r.URL.String() != c.Requests[i].URL {
					t.Error("original request differs")
				}
				status := c.Statuses[min(i, len(c.Statuses)-1)]
				if status == 0 {
					return nil, errors.New("fixture")
				}
				return &http.Response{StatusCode: status, Header: http.Header{"Content-Type": {"text/html; charset=utf-8"}}, Body: io.NopCloser(strings.NewReader(c.Body)), Request: r}, nil
			})}
			out, reserved, e := fetchFinalStaticDetail(context.Background(), client, finalDetailProfile(c.Provider, c.Source), func(context.Context, time.Duration) error { waits++; return nil })
			if (e != nil) != c.Failed || reserved != nil || calls != len(c.Requests) || waits != calls-1 {
				t.Fatal("original outcome differs", e, c.Failed, calls, len(c.Requests), waits)
			}
			if e == nil {
				for key, want := range c.Content {
					a, _ := json.Marshal(out[key])
					b, _ := json.Marshal(want)
					if string(a) != string(b) {
						t.Errorf("field %s differs %s / %s", key, a, b)
					}
				}
			}
		})
	}
}
func TestFinalStaticPublisherBeforeFailuresAndBounds(t *testing.T) {
	c := readFinalDetailCorpus(t)
	for provider, source := range c.Sources {
		for _, mode := range []string{"200_header", "503_header", "404_header", "redirect_header", "body", "broken_body", "prefix_broken", "oversize", "empty", "bad_redirect"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				calls, waits := 0, 0
				client := &http.Client{Transport: seekDetailTransport(func(r *http.Request) (*http.Response, error) {
					calls++
					status := 200
					h := http.Header{}
					raw := ""
					if strings.Contains(mode, "503") {
						status = 503
					}
					if strings.Contains(mode, "404") {
						status = 404
					}
					if strings.Contains(mode, "redirect") {
						status = 302
						h.Set("Location", "https://user:password@untrusted.example/")
					}
					if strings.Contains(mode, "header") {
						h.Set("TDM-Reservation", "1")
					}
					if mode == "body" {
						raw = `<meta name="tdm-reservation" content="1">`
					}
					if mode == "oversize" {
						raw = strings.Repeat("x", 25_000_001)
						if provider == "veryeast" {
							raw = strings.Repeat("x", 2<<20+1)
						}
					}
					body := io.ReadCloser(io.NopCloser(strings.NewReader(raw)))
					if mode == "prefix_broken" {
						body = io.NopCloser(&finalPrefixBrokenBody{})
					}
					if mode == "broken_body" {
						h.Set("TDM-Reservation", "1")
						body = io.NopCloser(&failingBody{})
					}
					return &http.Response{StatusCode: status, Header: h, Body: body, Request: r}, nil
				})}
				out, reserved, e := fetchFinalStaticDetail(context.Background(), client, finalDetailProfile(provider, source), func(context.Context, time.Duration) error { waits++; return nil })
				policyMode := strings.Contains(mode, "header") || mode == "body" || mode == "broken_body" || mode == "prefix_broken"
				if policyMode {
					if e != nil || reserved == nil || out != nil || calls != 1 || waits != 0 {
						t.Fatal("publisher signal lost", e, calls, waits)
					}
				} else if mode == "empty" && provider != "veryeast" {
					if e != nil || out["title"] != nil || calls != 1 {
						t.Fatal("empty behavior differs", e)
					}
				} else {
					expected := 1
					if mode == "empty" {
						expected = 3
					}
					if e == nil || calls != expected {
						t.Fatal("failure bound differs", e, calls, expected)
					}
				}
			})
		}
	}
}

type failingBody struct{}

func (*failingBody) Read([]byte) (int, error) { return 0, errors.New("broken fixture body") }

type finalPrefixBrokenBody struct{ sent bool }

func (b *finalPrefixBrokenBody) Read(p []byte) (int, error) {
	if b.sent {
		return 0, errors.New("broken fixture body")
	}
	b.sent = true
	return copy(p, `<meta name="tdm-reservation" content="1">`), nil
}
