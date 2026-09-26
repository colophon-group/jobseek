package workday

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type detailRoundTrip func(*http.Request) (*http.Response, error)

func (fn detailRoundTrip) RoundTrip(request *http.Request) (*http.Response, error) {
	return fn(request)
}

func detailResponse(status int, body string) *http.Response {
	return &http.Response{StatusCode: status, Header: http.Header{"Content-Type": []string{"application/json"}}, Body: io.NopCloser(strings.NewReader(body))}
}

func TestWorkdayDetailURLGuards(t *testing.T) {
	good := "https://tenant.wd5.myworkdayjobs.com/en-US/External/job/Engineer/JR001"
	api, tenant, err := workdayDetailAPIURL(good)
	if err != nil || api != "https://tenant.wd5.myworkdayjobs.com/wday/cxs/tenant/External/job/Engineer/JR001" || tenant != "tenant" {
		t.Fatalf("API URL = %q, tenant = %q, error = %v", api, tenant, err)
	}
	escaped, _, err := workdayDetailAPIURL("https://tenant.wd5.myworkdayjobs.com/External/job/Senior%20Engineer/JR001")
	if err != nil || escaped != "https://tenant.wd5.myworkdayjobs.com/wday/cxs/tenant/External/job/Senior%20Engineer/JR001" {
		t.Fatalf("escaped API URL = %q, error = %v", escaped, err)
	}
	for _, raw := range []string{
		"http://tenant.wd5.myworkdayjobs.com/External/job/JR001",
		"https://tenant.wd5.myworkdayjobs.com.evil.test/External/job/JR001",
		"https://tenant.wd5.myworkdayjobs.com/External/job/JR001?next=1",
		"https://tenant.wd5.myworkdayjobs.com/External/job/../admin",
		"https://tenant.wd5.myworkdayjobs.com/External/job/JR001%2Fadmin",
	} {
		if _, _, err := workdayDetailAPIURL(raw); err == nil {
			t.Errorf("accepted unsafe URL %q", raw)
		}
	}
}

func TestFetchWorkdayDetailStatusAndRetry(t *testing.T) {
	const source = "https://tenant.wd5.myworkdayjobs.com/External/job/Engineer/JR001"
	for _, test := range []struct {
		name      string
		statuses  []int
		bodies    []string
		gone      bool
		wantKind  string
		wantCalls int
	}{
		{"success", []int{200}, []string{`{"jobPostingInfo":{"title":"Engineer","jobReqId":"JR001"}}`}, false, "", 1},
		{"not_found", []int{404}, []string{`{}`}, true, "", 1},
		{"s22", []int{403}, []string{`{"errorCode":"S22"}`}, true, "", 1},
		{"other_forbidden", []int{403}, []string{`{"errorCode":"S01"}`}, false, "http", 1},
		{"invalid_recovers", []int{200, 200}, []string{`<html>`, `{"jobPostingInfo":{"title":"Engineer"}}`}, false, "", 2},
		{"invalid_exhausts", []int{200, 200, 200}, []string{`{}`, `{}`, `{}`}, false, "invalid_payload", 3},
	} {
		t.Run(test.name, func(t *testing.T) {
			calls := 0
			var delays []time.Duration
			client := &http.Client{Transport: detailRoundTrip(func(req *http.Request) (*http.Response, error) {
				if req.Method != http.MethodGet || req.URL.String() != "https://tenant.wd5.myworkdayjobs.com/wday/cxs/tenant/External/job/Engineer/JR001" || req.Header.Get("Accept") != "application/json" {
					t.Fatalf("unexpected detail request: %s %s", req.Method, req.URL)
				}
				index := calls
				calls++
				return detailResponse(test.statuses[index], test.bodies[index]), nil
			})}
			result, err := fetchWorkdayDetail(context.Background(), source, nil, client, func(_ context.Context, delay time.Duration) error { delays = append(delays, delay); return nil }, func() float64 { return 0.5 })
			if calls != test.wantCalls || result.Requests != calls || result.Responses != calls || result.TransportErrors != 0 || result.Gone != test.gone {
				t.Fatalf("calls=%d result=%+v", calls, result)
			}
			var fetchErr *DetailFetchError
			if test.wantKind == "" && err != nil || test.wantKind != "" && (!errors.As(err, &fetchErr) || fetchErr.Kind != test.wantKind) {
				t.Fatalf("error=%v, want kind=%q", err, test.wantKind)
			}
			if len(delays) != calls-1 || len(delays) > 0 && delays[0] != 500*time.Millisecond {
				t.Fatalf("delays=%v", delays)
			}
		})
	}
}

func TestFetchWorkdayDetailReservation(t *testing.T) {
	client := &http.Client{Transport: detailRoundTrip(func(*http.Request) (*http.Response, error) {
		response := detailResponse(200, `{}`)
		response.Header.Set("TDM-Reservation", "1")
		response.Header.Set("TDM-Policy", "https://example.test/policy")
		return response, nil
	})}
	result, err := fetchWorkdayDetail(context.Background(), "https://tenant.wd5.myworkdayjobs.com/External/job/JR001", nil, client, sleepContext, func() float64 { return 0.5 })
	var reserved *ReservationError
	if !errors.As(err, &reserved) || result.Requests != 1 || result.Responses != 1 || result.TDMPolicy != "https://example.test/policy" {
		t.Fatalf("result=%+v error=%v", result, err)
	}
}
