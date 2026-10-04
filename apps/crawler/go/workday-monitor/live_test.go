package workday

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

func TestInventoryPosterUsesVerifiedClientAcrossSitesWithoutFollowing303(t *testing.T) {
	var mu sync.Mutex
	calls := map[string]int{}
	client := &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		mu.Lock()
		calls[r.URL.Path]++
		n := calls[r.URL.Path]
		mu.Unlock()
		if r.Header.Get("User-Agent") != userAgent || r.Header.Get("Accept") != accept {
			t.Fatal("request policy changed")
		}
		if r.URL.Path == "/robots.txt" {
			if r.Method != "GET" {
				t.Fatal("robots method changed")
			}
			return liveResponse(200, "Sitemap: https://example.wd5.myworkdayjobs.com/External/siteMap\nSitemap: https://example.wd5.myworkdayjobs.com/Brand/siteMap\n", nil), nil
		}
		if r.Method != "POST" || r.Header.Get("Content-Type") != "application/json" {
			t.Fatal("Workday list POST policy changed")
		}
		var body inventoryFixtureRequest
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			return nil, err
		}
		if body.Limit != 20 || body.Offset != 0 {
			t.Fatal(body)
		}
		if n == 1 {
			return liveResponse(303, "", http.Header{"Location": []string{"https://other.example/converted-get"}}), nil
		}
		return liveResponse(200, string(page(1, "/job/Engineer_R-123")), nil), nil
	})}
	p, err := NewInventoryPoster(InventoryConfig{Site: Site{"example", "wd5", "External"}, AllSites: true}, client)
	if err != nil {
		t.Fatal(err)
	}
	p.sleep = func(context.Context, time.Duration) error { return nil }
	r, err := DiscoverInventory(context.Background(), InventoryConfig{Site: Site{"example", "wd5", "External"}, AllSites: true}, p.GetRobots, p.Post)
	if err != nil || len(r.URLs) != 1 || r.Sites != 2 || p.Requests != 5 || p.Responses != 5 {
		t.Fatalf("result=%+v requests=%d err=%v", r, p.Requests, err)
	}
	for _, bad := range []string{
		"https://other.wd5.myworkdayjobs.com/wday/cxs/example/External/jobs",
		"https://example.wd5.myworkdayjobs.com/wday/cxs/other/External/jobs",
		"https://example.wd5.myworkdayjobs.com/wday/cxs/example/External/jobs?redirect=1",
		"https://example.wd5.myworkdayjobs.com/wday/cxs/example/%45xternal/jobs",
	} {
		if _, err := p.Post(context.Background(), bad, nil); err == nil {
			t.Fatal("escaped tenant endpoint", bad)
		}
	}
	if client.CheckRedirect != nil {
		t.Fatal("caller HTTP client was mutated")
	}
}

func TestInventoryPosterExplicitSitesAndRobotsPublisherReservation(t *testing.T) {
	c := InventoryConfig{Site: Site{"example", "wd5", "External"}, AllSites: true, Sites: []string{"External", "Brand"}}
	client := &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		return liveResponse(200, "", http.Header{"Tdm-Reservation": []string{"1"}, "Tdm-Policy": []string{"https://example.org/policy"}}), nil
	})}
	p, err := NewInventoryPoster(c, client)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := p.Post(context.Background(), "https://example.wd5.myworkdayjobs.com/wday/cxs/example/Unselected/jobs", nil); err == nil || p.Requests != 0 {
		t.Fatal("explicit site selection was escaped")
	}
	_, err = p.GetRobots(context.Background(), "https://example.wd5.myworkdayjobs.com/robots.txt")
	var reservation *ReservationError
	if !errors.As(err, &reservation) || reservation.PolicyURL != "https://example.org/policy" {
		t.Fatal("robots policy refusal was hidden", err)
	}
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
