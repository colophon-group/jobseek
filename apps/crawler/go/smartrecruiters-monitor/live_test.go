package smartrecruiters

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type doerFunc func(*http.Request) (*http.Response, error)

func (f doerFunc) Do(r *http.Request) (*http.Response, error) { return f(r) }
func noPause(context.Context, time.Duration) error            { return nil }
func response(r *http.Request, status int, body string, headers http.Header) *http.Response {
	return &http.Response{StatusCode: status, Header: headers, Body: io.NopCloser(strings.NewReader(body)), Request: r}
}

func TestHTTPRetryPolicy(t *testing.T) {
	for _, status := range []int{301, 400, 401, 403, 404, 408, 410, 425, 429, 500, 503, 530} {
		t.Run(strconv.Itoa(status), func(t *testing.T) {
			n := 0
			client := doerFunc(func(r *http.Request) (*http.Response, error) {
				n++
				if r.Header.Get("User-Agent") != userAgent || r.Header.Get("Accept") != accept {
					t.Fatal("header parity")
				}
				return response(r, status, "", nil), nil
			})
			result, err := fetchWith(context.Background(), "https://careers.smartrecruiters.com/Acme", Object{}, client, noPause)
			expected := 1
			if status == 408 || status == 425 || status == 429 || status >= 500 {
				expected = 3
			}
			if err == nil || result.Failure == nil || result.Failure.Kind != "pagination" || result.Failure.Attempts != expected || result.Failure.Status != status || n != expected || result.Requests != expected || result.Responses != expected || result.URLs != nil {
				t.Fatalf("incorrect retry boundary: %+v %v calls=%d", result, err, n)
			}
		})
	}
}
func TestHTTPMalformedRetryAndRecovery(t *testing.T) {
	calls := 0
	client := doerFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		body := `{"content":[{"id":"1"}],"totalFound":1}`
		switch calls {
		case 1:
			body = `{invalid`
		case 2:
			body = `[]`
		}
		return response(r, 200, body, nil), nil
	})
	result, err := fetchWith(context.Background(), "https://careers.smartrecruiters.com/Acme", Object{}, client, noPause)
	if err != nil || calls != 3 || result.Requests != 3 || len(result.URLs) != 1 {
		t.Fatalf("%+v %v", result, err)
	}
}
func TestTDMBoundsAndPrecedence(t *testing.T) {
	cases := []struct {
		name, header, body, source string
		blocked                    bool
	}{
		{"header", "1", `{"content":[],"totalFound":0}`, "header", true},
		{"literal-only", "01", `{"content":[],"totalFound":0}`, "", false},
		{"meta", "0", `<meta content='&#49;' name='TDM-Reservation'><meta name=tdm-policy content=https://example.test/policy>`, "meta", true},
		{"last-opt-in", "", `<meta name=tdm-reservation content=1><meta name=tdm-reservation content=0>`, "", false},
		{"comment", "", `<!-- <meta name=tdm-reservation content=1> -->`, "", false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := &fetcher{token: "Acme", client: doerFunc(func(r *http.Request) (*http.Response, error) {
				return response(r, 200, c.body, http.Header{"Tdm-Reservation": {c.header}, "Tdm-Policy": {"header-policy"}}), nil
			}), pause: noPause}
			_, _, _, err := f.once(context.Background(), ListURL("Acme")+"?limit=100&offset=0", ListResponseMaxBytes)
			var failure *Failure
			blocked := errors.As(err, &failure) && failure.Kind == "tdm"
			if blocked != c.blocked || blocked && failure.Source != c.source {
				t.Fatalf("policy mismatch: %v", err)
			}
			if c.source == "header" && f.result.Bytes != 0 {
				t.Fatal("reserved header consumed body")
			}
			if c.source == "meta" && failure.Policy != "https://example.test/policy" {
				t.Fatal("meta policy lost")
			}
		})
	}
}

func TestMonitorPositiveHeaderBeforeStatusAndBody(t *testing.T) {
	for _, status := range []int{200, 302, 404, 429, 503} {
		calls := 0
		client := doerFunc(func(r *http.Request) (*http.Response, error) {
			calls++
			return response(r, status, "invalid JSON", http.Header{"Tdm-Reservation": {"1"}, "Tdm-Policy": {"publisher-policy"}}), nil
		})
		result, err := FetchWithClient(context.Background(), "https://careers.smartrecruiters.com/fixture", Object{}, client)
		var failure *Failure
		if !errors.As(err, &failure) || failure.Kind != "tdm" || failure.Source != "header" || failure.Policy != "publisher-policy" || calls != 1 || result.Bytes != 0 || len(result.URLs) != 0 {
			t.Fatalf("header reservation became status failure or consumed body: %+v %v", result, err)
		}
	}
}
func TestBodyLimitIsNotRetried(t *testing.T) {
	calls := 0
	f := &fetcher{token: "Acme", client: doerFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		return response(r, 200, strings.Repeat("x", 30), nil), nil
	}), pause: noPause}
	_, err := f.get(context.Background(), ListURL("Acme"), 10)
	if calls != 1 || f.result.Bytes != 11 {
		t.Fatalf("unbounded/retried body: %d %d", calls, f.result.Bytes)
	}
	var failure *Failure
	if !errors.As(err, &failure) || failure.Kind != "body_limit" {
		t.Fatal(err)
	}
}
func TestTruncationProtectsUnseenTail(t *testing.T) {
	calls := 0
	get := func(_ context.Context, endpoint string, _ int) (Object, error) {
		offset := calls * 100
		calls++
		items := make([]any, 100)
		for i := range items {
			items[i] = Object{"id": fmt.Sprint(offset + i)}
		}
		return Object{"content": items, "totalFound": json.Number("50001")}, nil
	}
	inventory, err := Discover(context.Background(), Options{Token: "Acme"}, get, noPause)
	if err != nil || !inventory.Truncated || len(inventory.URLs) != MaxJobs || calls != 500 {
		t.Fatalf("%d urls %d calls %v", len(inventory.URLs), calls, err)
	}
}
func TestDetailFanoutIsBoundedAndCancellationJoins(t *testing.T) {
	items := make([]Object, 40)
	for i := range items {
		items[i] = Object{"id": fmt.Sprint(i)}
	}
	var active, peak atomic.Int32
	entered := make(chan struct{}, 40)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, err := fetchDetails(ctx, Options{Token: "Acme"}, items, func(ctx context.Context, _ string, _ int) (Object, error) {
			n := active.Add(1)
			defer active.Add(-1)
			for p := peak.Load(); n > p && !peak.CompareAndSwap(p, n); p = peak.Load() {
			}
			entered <- struct{}{}
			<-ctx.Done()
			return nil, ctx.Err()
		})
		done <- err
	}()
	for i := 0; i < 12; i++ {
		select {
		case <-entered:
		case <-time.After(time.Second):
			t.Fatal("fanout failed")
		}
	}
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancel accepted")
		}
	case <-time.After(time.Second):
		t.Fatal("cancellation failed")
	}
	if peak.Load() != 12 || active.Load() != 0 {
		t.Fatalf("peak=%d active=%d", peak.Load(), active.Load())
	}
}
