package teamtailorrss

import (
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

type scriptedDoer struct {
	requests []string
	statuses []int
	bodies   []string
}

func (s *scriptedDoer) Do(request *http.Request) (*http.Response, error) {
	index := len(s.requests)
	s.requests = append(s.requests, request.URL.String())
	if index >= len(s.statuses) {
		return nil, fmt.Errorf("unexpected request %s", request.URL)
	}
	return &http.Response{
		StatusCode: s.statuses[index], Body: io.NopCloser(strings.NewReader(s.bodies[index])),
		Header: http.Header{}, Request: request,
	}, nil
}

func feed(items int) string {
	var body strings.Builder
	body.WriteString(`<rss version="2.0"><channel>`)
	for index := 0; index < items; index++ {
		fmt.Fprintf(&body, `<item><link>https://careers.example.com/jobs/%d</link><title>Job %d</title></item>`, index, index)
	}
	body.WriteString(`</channel></rss>`)
	return body.String()
}

func TestFetchPagesAndRetries(t *testing.T) {
	doer := &scriptedDoer{
		statuses: []int{400, 200, 200},
		bodies:   []string{"temporary", feed(100), feed(1)},
	}
	result, err := Fetch(context.Background(), doer, "https://careers.example.com/jobs.rss")
	if err != nil {
		t.Fatal(err)
	}
	if result.Requests != 3 || result.Responses != 3 || len(result.Jobs) != 101 || result.Truncated {
		t.Fatalf("unexpected result: requests=%d responses=%d jobs=%d truncated=%v", result.Requests, result.Responses, len(result.Jobs), result.Truncated)
	}
	if doer.requests[0] != "https://careers.example.com/jobs.rss?offset=0&per_page=100" || doer.requests[2] != "https://careers.example.com/jobs.rss?offset=100&per_page=100" {
		t.Fatalf("wrong pagination: %v", doer.requests)
	}
}

func TestFetchNegotiatesHTTP2WithCustomDialer(t *testing.T) {
	var protocol atomic.Int32
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		protocol.Store(int32(request.ProtoMajor))
		w.Header().Set("Content-Type", "application/rss+xml")
		fmt.Fprint(w, feed(1))
	}))
	server.EnableHTTP2 = true
	server.StartTLS()
	defer server.Close()
	client := newClient()
	defer client.CloseIdleConnections()
	transport := client.Transport.(*http.Transport)
	transport.DialContext = func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "tcp", server.Listener.Addr().String())
	}
	transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} // loopback test certificate
	result, err := Fetch(context.Background(), client, "https://careers.example.com/jobs.rss")
	if err != nil {
		t.Fatal(err)
	}
	if protocol.Load() != 2 || result.Responses != 1 || len(result.Jobs) != 1 {
		t.Fatalf("protocol=%d responses=%d jobs=%d", protocol.Load(), result.Responses, len(result.Jobs))
	}
}

func TestFetchRejectsUnsafeFeedURL(t *testing.T) {
	for _, raw := range []string{
		"http://careers.example.com/jobs.rss",
		"https://user:pass@careers.example.com/jobs.rss",
		"https://careers.example.com:8080/jobs.rss",
		"https://careers.example.com/admin",
		"https://careers.example.com/jobs.rss#fragment",
	} {
		if _, err := validFeedURL(raw); err == nil {
			t.Fatalf("accepted unsafe feed URL %q", raw)
		}
	}
}
