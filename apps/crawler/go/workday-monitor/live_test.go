package workday

import (
	"context"
	"crypto/tls"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

func liveResponse(status int, body string, headers http.Header) *http.Response {
	return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(body)), Header: headers}
}

func testPoster(t *testing.T, trip roundTripFunc) *LivePoster {
	t.Helper()
	poster, err := NewLivePoster(Site{Company: "example", Instance: "wd5", Name: "External"})
	if err != nil {
		t.Fatal(err)
	}
	poster.client.Transport = trip
	poster.sleep = func(context.Context, time.Duration) error { return nil }
	poster.random = func() float64 { return 0.5 }
	return poster
}

func TestLivePosterHandlesHTTP2WorkdayEdge(t *testing.T) {
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if request.ProtoMajor != 2 {
			t.Errorf("Workday edge negotiated %s; want HTTP/2", request.Proto)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"total":0,"jobPostings":[]}`)
	}))
	server.EnableHTTP2 = true
	server.StartTLS()
	defer server.Close()

	poster, err := NewLivePoster(Site{Company: "example", Instance: "wd5", Name: "External"})
	if err != nil {
		t.Fatal(err)
	}
	transport := poster.client.Transport.(*http.Transport)
	transport.DialContext = func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "tcp", server.Listener.Addr().String())
	}
	transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} // Local test server only.
	_, err = poster.Post(context.Background(), poster.listURL, []byte(`{"limit":20,"offset":0}`))
	if err != nil {
		t.Fatal(err)
	}
	if poster.Requests != 1 || poster.Responses != 1 || poster.TransportErrors != 0 {
		t.Fatalf("requests=%d responses=%d transport_errors=%d", poster.Requests, poster.Responses, poster.TransportErrors)
	}
}

func TestLivePosterRetries303WithoutFollowingAndKeepsRequestPolicy(t *testing.T) {
	requests := 0
	poster := testPoster(t, func(request *http.Request) (*http.Response, error) {
		requests++
		if request.Method != http.MethodPost || request.URL.String() != "https://example.wd5.myworkdayjobs.com/wday/cxs/example/External/jobs" {
			t.Fatalf("unexpected request: %s %s", request.Method, request.URL)
		}
		if request.Header.Get("User-Agent") != userAgent || request.Header.Get("Accept") != accept || request.Header.Get("Content-Type") != "application/json" {
			t.Fatalf("request headers differ from the Python Workday policy: %v", request.Header)
		}
		body, _ := io.ReadAll(request.Body)
		if string(body) != `{"limit":20,"offset":0}` {
			t.Fatalf("request body = %s", body)
		}
		if requests == 1 {
			return liveResponse(303, "", http.Header{"Location": []string{"https://other.example/jobs"}}), nil
		}
		return liveResponse(200, `{"total":0,"jobPostings":[]}`, nil), nil
	})
	got, err := poster.Post(context.Background(), poster.listURL, []byte(`{"limit":20,"offset":0}`))
	if err != nil || requests != 2 || poster.Requests != 2 || !strings.Contains(string(got), `"total":0`) {
		t.Fatalf("body=%s requests=%d err=%v", got, requests, err)
	}
}

func TestLivePosterHonorsReservationWithoutRetry(t *testing.T) {
	poster := testPoster(t, func(*http.Request) (*http.Response, error) {
		return liveResponse(200, `{"total":0}`, http.Header{
			"Tdm-Reservation": []string{"1"},
			"Tdm-Policy":      []string{"https://example.org/policy"},
		}), nil
	})
	_, err := poster.Post(context.Background(), poster.listURL, []byte(`{"limit":20,"offset":0}`))
	var reserved *ReservationError
	if !errors.As(err, &reserved) || reserved.PolicyURL != "https://example.org/policy" || poster.Requests != 1 {
		t.Fatalf("reservation=%v requests=%d", err, poster.Requests)
	}
}

func TestLivePosterFailsFastOnNonRetryableStatus(t *testing.T) {
	poster := testPoster(t, func(*http.Request) (*http.Response, error) {
		return liveResponse(403, "denied", nil), nil
	})
	_, err := poster.Post(context.Background(), poster.listURL, []byte(`{"limit":20,"offset":0}`))
	var failed *FetchError
	if !errors.As(err, &failed) || failed.Status != 403 || failed.Attempts != 1 || poster.Requests != 1 {
		t.Fatalf("failure=%v requests=%d", err, poster.Requests)
	}
}

func TestLivePosterRetriesInvalidJSONAndExhausted429(t *testing.T) {
	count := 0
	poster := testPoster(t, func(*http.Request) (*http.Response, error) {
		count++
		if count == 1 {
			return liveResponse(200, "<html>temporary</html>", nil), nil
		}
		return liveResponse(429, "limited", nil), nil
	})
	_, err := poster.Post(context.Background(), poster.listURL, []byte(`{"limit":20,"offset":0}`))
	var failed *FetchError
	if !errors.As(err, &failed) || failed.Status != 429 || failed.Attempts != 3 || count != 3 {
		t.Fatalf("failure=%v requests=%d", err, count)
	}
}

func TestLivePosterRejectsAnotherEndpointBeforeNetwork(t *testing.T) {
	poster := testPoster(t, func(*http.Request) (*http.Response, error) {
		t.Fatal("request escaped configured endpoint")
		return nil, nil
	})
	_, err := poster.Post(context.Background(), "https://example.wd5.myworkdayjobs.com/other", nil)
	if err == nil || poster.Requests != 0 {
		t.Fatalf("error=%v requests=%d", err, poster.Requests)
	}
}
