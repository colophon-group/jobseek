package jsonld

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
)

type doerFunc func(*http.Request) (*http.Response, error)

func (f doerFunc) Do(r *http.Request) (*http.Response, error) { return f(r) }
func reply(r *http.Request, status int, body string) *http.Response {
	return &http.Response{StatusCode: status, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(body)), Request: r}
}
func noWait(context.Context, time.Duration) error { return nil }

const jobHTML = `<script type="application/ld+json">{"@type":"JobPosting","title":"Engineer","description":"<p>Build</p>"}</script>`

func TestTransportRetriesAndFailureSemantics(t *testing.T) {
	for _, c := range []struct {
		name, url string
		statuses  []int
		wantCalls int
		wantKind  string
	}{
		{"403 recovers", "https://example.com/job", []int{403, 200}, 2, ""},
		{"403 exhausts", "https://example.com/job", []int{403, 403}, 2, "status"},
		{"Avature406", "https://jobs.example.com/careers/JobDetail/1", []int{406, 406, 200}, 3, ""},
		{"ordinary406", "https://example.com/job", []int{406}, 1, "status"},
		{"gone404", "https://example.com/job", []int{404}, 1, "status"},
		{"gone410", "https://example.com/job", []int{410}, 1, "status"},
		{"rate429", "https://example.com/job", []int{429}, 1, "status"},
		{"server503", "https://example.com/job", []int{503}, 1, "status"},
	} {
		t.Run(c.name, func(t *testing.T) {
			calls := 0
			client := doerFunc(func(r *http.Request) (*http.Response, error) {
				status := c.statuses[calls]
				calls++
				return reply(r, status, jobHTML), nil
			})
			result, err := fetchDetail(context.Background(), client, Request{URL: c.url}, noWait)
			if calls != c.wantCalls || result.ErrorKind != c.wantKind || (err != nil) != (c.wantKind != "") {
				t.Fatalf("calls=%d result=%+v err=%v", calls, result, err)
			}
		})
	}
	calls := 0
	_, err := fetchDetail(context.Background(), doerFunc(func(r *http.Request) (*http.Response, error) { calls++; return nil, errors.New("network") }), Request{URL: "https://example.com/job"}, noWait)
	if err == nil || calls != 1 {
		t.Fatal("transport failure retried")
	}
}
func TestContentRetryAndICIMS(t *testing.T) {
	calls := 0
	client := doerFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		if calls == 1 {
			return reply(r, 200, "no content"), nil
		}
		return reply(r, 200, jobHTML), nil
	})
	result, err := fetchDetail(context.Background(), client, Request{URL: "https://example.com/job"}, noWait)
	if err != nil || calls != 2 || result.Content["title"] != "Engineer" {
		t.Fatalf("%+v %v", result, err)
	}
	calls = 0
	client = doerFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.URL.RawQuery == "" {
			return reply(r, 200, `<iframe src="?in_iframe=1"></iframe><iframe src="https://evil.test/jobs/1?in_iframe=1"></iframe>`), nil
		}
		return reply(r, 200, jobHTML), nil
	})
	result, err = fetchDetail(context.Background(), client, Request{URL: "https://careers.test.icims.com/jobs/1"}, noWait)
	if err != nil || calls != 2 || result.Content["title"] != "Engineer" {
		t.Fatalf("%+v %v", result, err)
	}
}
func TestPolicyAndRedirectBoundaries(t *testing.T) {
	for _, source := range []string{"header", "meta"} {
		t.Run(source, func(t *testing.T) {
			calls := 0
			result, err := fetchDetail(context.Background(), doerFunc(func(r *http.Request) (*http.Response, error) {
				calls++
				resp := reply(r, 200, `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="license">`)
				if source == "header" {
					resp.Header.Set("TDM-Reservation", "1")
					resp.Header.Set("TDM-Policy", "license")
				}
				return resp, nil
			}), Request{URL: "https://example.com/job"}, noWait)
			if err == nil || calls != 1 || result.ErrorKind != "tdm" || result.TDMSource != source || result.TDMPolicy != "license" {
				t.Fatalf("%+v %v", result, err)
			}
		})
	}
	calls := 0
	stats := FetchResult{}
	_, err := fetchPage(context.Background(), doerFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		resp := reply(r, 302, "")
		resp.Header.Set("Location", "https://example.com/next")
		return resp, nil
	}), "https://example.com/job", &stats, nil)
	if err == nil || calls != 21 {
		t.Fatalf("redirect calls=%d err=%v", calls, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if sleep(ctx, time.Hour) == nil {
		t.Fatal("cancel ignored")
	}
}
func TestFrozenPythonDescriptions(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_descriptions.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, HTML, Selector, Expected string
		Error                          bool `json:"expected_error"`
	}
	if json.Unmarshal(raw, &cases) != nil {
		t.Fatal("fixture")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			actual, err := selectedDescription([]byte(c.HTML), c.Selector)
			if c.Error {
				if err == nil {
					t.Fatal("expected error")
				}
				return
			}
			if err != nil || actual != c.Expected {
				t.Fatalf("got %q %v want %q", actual, err, c.Expected)
			}
		})
	}
}
func TestConfigAndWireEncoding(t *testing.T) {
	for _, config := range []map[string]any{{"render": true}, {"proxy": true}, {"skip_ssl": true}, {"unknown": true}, {"transport_attempts": true}, {"description_selector": "[bad"}} {
		if ValidateConfig(config) == nil {
			t.Fatalf("accepted %v", config)
		}
	}
	if got := string(decodedBody([]byte{0xe9}, "text/html; charset=iso-8859-1")); got != "é" {
		t.Fatal(got)
	}
	if got := string(decodedBody([]byte{0x80}, "text/html; charset=iso-8859-1")); got != "\u0080" {
		t.Fatal(got)
	}
	if validEndpoint("https://user:password@example.com/") || validEndpoint("file:///tmp/job") || validEndpoint("http://example.com:81/") {
		t.Fatal("unsafe endpoint accepted")
	}
}

func TestSourceFragmentIsKeptForDefaultsAndNotSentOnWire(t *testing.T) {
	rawURL := "https://example.com/job#JobEntry"
	config := map[string]any{"defaults_by_url": map[string]any{rawURL: map[string]any{"locations": []any{"Vienna"}}}}
	client := doerFunc(func(r *http.Request) (*http.Response, error) {
		var wire strings.Builder
		if err := r.Write(&wire); err != nil {
			t.Fatal(err)
		}
		if strings.Contains(wire.String(), "#JobEntry") {
			t.Fatal("fragment sent to publisher")
		}
		return reply(r, 200, jobHTML), nil
	})
	result, err := fetchDetail(context.Background(), client, Request{URL: rawURL, Config: config}, noWait)
	if err != nil || result.Content["locations"].([]any)[0] != "Vienna" {
		t.Fatalf("%+v %v", result, err)
	}
}
