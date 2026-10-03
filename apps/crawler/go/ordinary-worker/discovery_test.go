package worker

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"testing"

	greenhouse "github.com/colophon-group/jobseek/apps/crawler/go/greenhouse-monitor"
)

type testRoundTripper func(*http.Request) (*http.Response, error)

func (f testRoundTripper) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func discoveryKind(err error) string {
	if err == nil {
		return ""
	}
	var failure *DiscoveryError
	if !errors.As(err, &failure) {
		return "untyped_error"
	}
	return failure.Kind
}

func TestGreenhouseDiscoveryActualPythonOracle(t *testing.T) {
	body, err := os.ReadFile("testdata/python_discovery.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Kind string
		FinalURL   string  `json:"final_url"`
		PolicyURL  *string `json:"policy_url"`
		Requests   []string
		Jobs       []greenhouse.Job
		Truncated  bool
		Responses  []struct {
			Status     int
			BodyBase64 string `json:"body_base64"`
			Headers    []struct {
				Name        string
				ValueBase64 string `json:"value_base64"`
			}
		}
	}
	if err := json.Unmarshal(body, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) < 50 {
		t.Fatal("missing actual Python HTTP captures")
	}
	for _, item := range cases {
		t.Run(item.Name, func(t *testing.T) {
			var requests []string
			closed := 0
			client := &http.Client{
				CheckRedirect: func(_ *http.Request, via []*http.Request) error {
					if len(via) > 20 {
						return errors.New("redirect limit")
					}
					return nil
				},
				Transport: testRoundTripper(func(request *http.Request) (*http.Response, error) {
					requests = append(requests, request.URL.String())
					if request.Method != http.MethodGet || request.Header.Get("User-Agent") != ordinaryUserAgent || request.Header.Get("Accept") != ordinaryAccept {
						t.Fatal("ordinary request defaults/method changed")
					}
					if len(item.Responses) == 0 {
						return nil, errors.New("private upstream diagnostic")
					}
					if len(requests) > len(item.Responses) {
						t.Fatal("unexpected retry/request")
					}
					row := item.Responses[len(requests)-1]
					body, err := base64.StdEncoding.DecodeString(row.BodyBase64)
					if err != nil {
						t.Fatal(err)
					}
					header := http.Header{}
					for _, pair := range row.Headers {
						value, err := base64.StdEncoding.DecodeString(pair.ValueBase64)
						if err != nil {
							t.Fatal(err)
						}
						header.Add(pair.Name, string(value))
					}
					return &http.Response{StatusCode: row.Status, Header: header, Body: &countedBody{Reader: bytes.NewReader(body), close: func() { closed++ }}, Request: request}, nil
				}),
			}
			got, err := DiscoverGreenhouse(context.Background(), client, "fixture")
			if discoveryKind(err) != item.Kind || !reflect.DeepEqual(requests, item.Requests) {
				t.Fatalf("Python outcome/request mismatch: %v / %v want %s / %v", err, requests, item.Kind, item.Requests)
			}
			if len(item.Responses) != 0 && closed != len(requests) {
				t.Fatalf("response body leak: closed %d / requests %d", closed, len(requests))
			}
			if item.FinalURL == "" {
				if got.Response != nil {
					t.Fatal("failed request supplied a completed response")
				}
			} else if got.Response == nil || got.Response.FinalURL() != item.FinalURL || got.Response.Endpoint() != "https://boards-api.greenhouse.io/v1/boards/fixture/jobs?content=true" {
				t.Fatalf("final resource evidence changed: %+v want %s", got.Response, item.FinalURL)
			}
			if item.Kind == "publisher_reserved" {
				if !got.Response.Reserved() || !reflect.DeepEqual(got.Response.PolicyURL(), item.PolicyURL) {
					t.Fatalf("publisher signal/policy differs: %+v expected %v", got.Response, item.PolicyURL)
				}
				if value := got.Response.PolicyURL(); value != nil {
					*value = "changed"
					if !reflect.DeepEqual(got.Response.PolicyURL(), item.PolicyURL) {
						t.Fatal("detached caller changed final-resource observation")
					}
				}
			}
			if item.Kind == "" {
				if !reflect.DeepEqual(got.Inventory.Jobs, item.Jobs) || got.Inventory.Truncated != item.Truncated {
					t.Fatalf("Python rich inventory differs: %+v want %+v", got.Inventory, item.Jobs)
				}
			} else if len(got.Inventory.Jobs) != 0 || got.Inventory.Truncated {
				t.Fatal("failed discovery supplied a partial inventory")
			}
			if err != nil && (strings.Contains(err.Error(), "private") || strings.Contains(err.Error(), "policy.invalid") || strings.Contains(err.Error(), "redirect.invalid")) {
				t.Fatal("upstream diagnostics crossed error boundary")
			}
		})
	}
}

type countedBody struct {
	io.Reader
	close func()
}

func (b *countedBody) Close() error { b.close(); return nil }

func TestGreenhouseDiscoveryReadFailureAndCancellationRejectInventory(t *testing.T) {
	for _, scenario := range []string{"read_error", "body_limit", "cancel_during_read", "invalid_token", "nil_client", "cancel_before_request"} {
		t.Run(scenario, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			requests, closed := 0, 0
			client := &http.Client{Transport: testRoundTripper(func(request *http.Request) (*http.Response, error) {
				requests++
				var reader io.Reader = &failingReader{}
				if scenario == "body_limit" {
					reader = io.LimitReader(zeroReader{}, greenhouse.MaxBodyBytes+1)
				}
				if scenario == "cancel_during_read" {
					reader = &cancelReader{cancel: cancel}
				}
				return &http.Response{StatusCode: 404, Header: http.Header{"Tdm-Reservation": []string{"1"}}, Request: request, Body: &countedBody{Reader: reader, close: func() { closed++ }}}, nil
			})}
			token := "fixture"
			want := "body_failed"
			if scenario == "body_limit" {
				want = "body_limit"
			}
			if scenario == "invalid_token" {
				token, want = "../wrong", "invalid_configuration"
			}
			if scenario == "nil_client" {
				client, want = nil, "invalid_configuration"
			}
			if scenario == "cancel_before_request" {
				cancel()
				want = "request_failed"
			}
			got, err := DiscoverGreenhouse(ctx, client, token)
			if discoveryKind(err) != want || got.Response != nil || len(got.Inventory.Jobs) != 0 || closed != requests {
				t.Fatalf("incomplete body supplied completed policy/provider/inventory: %+v %v closed=%d requests=%d", got, err, closed, requests)
			}
			if strings.HasPrefix(scenario, "cancel_") && !errors.Is(err, context.Canceled) {
				t.Fatal("cancellation lost")
			}
			if strings.Contains(err.Error(), "private") {
				t.Fatal("body diagnostic exposed")
			}
		})
	}
}

type failingReader struct{}

func (*failingReader) Read([]byte) (int, error) { return 0, errors.New("private body error") }

type zeroReader struct{}

func (zeroReader) Read(p []byte) (int, error) { clear(p); return len(p), nil }

type cancelReader struct{ cancel context.CancelFunc }

func (r *cancelReader) Read([]byte) (int, error) { r.cancel(); return 0, context.Canceled }

// A real local socket proof supplements response replay. The fixture adapter
// rewrites only the physical destination; the monitor sees the original
// resource, redirects and process-owned cookie jar. It is never a live guard.
func TestGreenhouseDiscoveryRealHTTPRedirectCookiesAndSharedClient(t *testing.T) {
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.URL.Path == "/v1/boards/fixture/jobs" {
			if r.URL.Query().Get("content") != "true" {
				t.Error("missing content=true")
			}
			http.SetCookie(w, &http.Cookie{Name: "session", Value: "retained", Path: "/"})
			w.Header().Set("Location", "/final")
			w.WriteHeader(302)
			return
		}
		cookie, err := r.Cookie("session")
		if err != nil || cookie.Value != "retained" {
			t.Error("redirect lost process-owned cookie")
		}
		w.WriteHeader(202)
		_, _ = io.WriteString(w, `{"jobs":[{"absolute_url":"https://example.com/jobs/1","title":"Engineer"}]}`)
	}))
	t.Cleanup(server.Close)
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	physical := server.Client()
	t.Cleanup(physical.CloseIdleConnections)
	client := &http.Client{Jar: jar, Transport: testRoundTripper(func(original *http.Request) (*http.Response, error) {
		request := original.Clone(original.Context())
		target := *original.URL
		target.Scheme = "http"
		target.Host = strings.TrimPrefix(server.URL, "http://")
		request.URL = &target
		response, err := physical.Transport.RoundTrip(request)
		if response != nil {
			response.Request = original
		}
		return response, err
	})}
	for range 2 {
		got, err := DiscoverGreenhouse(context.Background(), client, "fixture")
		if err != nil || len(got.Inventory.Jobs) != 1 || got.Response.Status() != 202 || got.Response.FinalURL() != "https://boards-api.greenhouse.io/final" {
			t.Fatalf("real redirect/cookie/success pipeline: %+v %v", got, err)
		}
	}
	if requests != 4 {
		t.Fatalf("single GET unexpectedly retried: %d requests", requests)
	}
}
