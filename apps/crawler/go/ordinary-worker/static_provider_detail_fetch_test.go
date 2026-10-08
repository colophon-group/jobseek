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
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type staticDetailCorpus struct {
	Cases []struct {
		Provider, Name, Body string
		Content              map[string]any
		Failed               bool
	}
	Requests []struct {
		Provider, Name, Source, Body string
		Statuses                     []int
		Requests                     []struct{ Method, URL string }
		Content                      map[string]any
		Failed                       bool
	}
}

func readStaticDetailCorpus(t *testing.T) staticDetailCorpus {
	t.Helper()
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_static_provider_detail.json")
	var corpus staticDetailCorpus
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Requests) != 63 {
		t.Fatal("actual Python request traces missing", err, len(corpus.Requests))
	}
	return corpus
}
func staticDetailTestProfile(t *testing.T, provider, source string) queue.WorkdayDetailProfile {
	t.Helper()
	o, err := api.StaticProviderDetailOptionsForSource(provider, source)
	if err != nil {
		t.Fatal(err)
	}
	return queue.WorkdayDetailProfile{Profile: map[string]string{"linkedin": "linkedin.guest-detail/v1", "jazzhr": "jazzhr.public-detail/v1", "taleo": "taleo.enterprise-detail/v1"}[provider], SourceURL: source, Endpoint: o.Endpoint}
}

func TestStaticProviderDetailActualPythonRequestsRetriesAndFields(t *testing.T) {
	corpus := readStaticDetailCorpus(t)
	for _, c := range corpus.Requests {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			p := staticDetailTestProfile(t, c.Provider, c.Source)
			calls, waits := 0, 0
			client := &http.Client{Transport: seekDetailTransport(func(r *http.Request) (*http.Response, error) {
				index := calls
				calls++
				if index >= len(c.Requests) {
					t.Error("extra request")
				} else if r.Method != c.Requests[index].Method || r.URL.String() != c.Requests[index].URL {
					t.Error("request differs from Python", r.Method, r.URL.String(), c.Requests[index])
				}
				status := c.Statuses[min(index, len(c.Statuses)-1)]
				if status == 0 {
					return nil, errors.New("fixture transport")
				}
				return &http.Response{StatusCode: status, Header: http.Header{"Content-Type": {"text/html; charset=utf-8"}}, Body: io.NopCloser(strings.NewReader(c.Body)), Request: r}, nil
			})}
			out, reserved, err := fetchStaticProviderDetail(context.Background(), client, p, func(_ context.Context, delay time.Duration) error {
				base := 500 * time.Millisecond
				if c.Provider == "linkedin" {
					base = 1500 * time.Millisecond
				}
				lower := base * time.Duration(1<<waits) / 2
				if delay < lower || delay >= lower*3 {
					t.Error("backoff differs", delay, lower)
				}
				waits++
				return nil
			})
			if (err != nil) != c.Failed || reserved != nil || calls != len(c.Requests) || waits != calls-1 {
				t.Fatal("actual Python outcome differs", err, reserved, calls, len(c.Requests), waits, c.Failed)
			}
			if err == nil {
				for key, want := range c.Content {
					got, _ := json.Marshal(out[key])
					expected, _ := json.Marshal(want)
					if string(got) != string(expected) {
						t.Errorf("%s differs: %s / %s", key, got, expected)
					}
				}
			}
			if c.Name == "404" || c.Name == "410" {
				var closed *executor.NavigationHTTPError
				if !errors.As(err, &closed) || int(closed.Status) != c.Statuses[0] {
					t.Fatal("closed job signal lost", err)
				}
			}
		})
	}
}

func TestStaticProviderDetailPublisherBeforeFailureRetryAndRedirect(t *testing.T) {
	corpus := readStaticDetailCorpus(t)
	for _, provider := range []string{"linkedin", "jazzhr", "taleo"} {
		source := map[string]string{"linkedin": "https://ch.linkedin.com/jobs/view/title-123", "jazzhr": "https://fixture.applytojob.com/apply/jobs/details/123", "taleo": "https://fixture.taleo.net/careersection/2/jobdetail.ftl?job=123"}[provider]
		p := staticDetailTestProfile(t, provider, source)
		body := ""
		for _, c := range corpus.Requests {
			if c.Provider == provider && c.Name == "200" {
				body = c.Body
			}
		}
		for _, mode := range []string{"header", "503_header", "404_header", "redirect_header", "body", "503_body", "broken_body", "redirect", "bad_redirect", "redirect_loop"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				calls, waits := 0, 0
				client := &http.Client{Transport: seekDetailTransport(func(r *http.Request) (*http.Response, error) {
					calls++
					status := 200
					raw := body
					headers := http.Header{"Content-Type": {"text/html"}}
					if strings.Contains(mode, "503") {
						status = 503
					}
					if strings.Contains(mode, "404") {
						status = 404
					}
					if strings.Contains(mode, "header") {
						headers.Set("TDM-Reservation", "1")
						raw = "invalid"
					}
					if strings.Contains(mode, "body") {
						raw = `<meta name="tdm-reservation" content="1">`
					}
					if strings.Contains(mode, "redirect") && (calls == 1 || mode == "redirect_loop") {
						status = 302
						headers.Set("Location", "/redirected")
						if mode == "bad_redirect" {
							headers.Set("Location", "file:///private")
						}
					}
					var b io.ReadCloser = io.NopCloser(strings.NewReader(raw))
					if mode == "broken_body" {
						b = seekBrokenBody{strings.NewReader(raw)}
					}
					return &http.Response{StatusCode: status, Header: headers, Body: b, Request: r}, nil
				})}
				out, reserved, err := fetchStaticProviderDetail(context.Background(), client, p, func(context.Context, time.Duration) error { waits++; return nil })
				switch mode {
				case "redirect":
					if err != nil || reserved != nil || calls != 2 || waits != 0 || out["title"] == nil {
						t.Fatal(out, reserved, err, calls, waits)
					}
				case "bad_redirect", "redirect_loop":
					if err == nil || reserved != nil || len(out) != 0 || waits != 0 {
						t.Fatal("invalid redirect succeeded or retried", out, reserved, err, calls, waits)
					}
				default:
					if reserved == nil || err != nil || len(out) != 0 || calls != 1 || waits != 0 {
						t.Fatal("publisher reservation lost or retried", out, reserved, err, calls, waits)
					}
				}
			})
		}
	}
}
