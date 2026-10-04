package worker

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"

	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
)

func TestWorkdayDiscoveryUsesNativeTransportAndURLOnlyContract(t *testing.T) {
	count := 0
	verified := &VerifiedDirectHTTP{client: &http.Client{Transport: richRoundTrip(func(r *http.Request) (*http.Response, error) {
		count++
		if r.Method != "POST" || r.URL.String() != "https://example.wd5.myworkdayjobs.com/wday/cxs/example/External/jobs" {
			t.Fatalf("detail or foreign request made: %s %s", r.Method, r.URL)
		}
		return &http.Response{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(`{"total":2,"jobPostings":[{"externalPath":"/job/Engineer_R-123"},{"externalPath":"/job/Other_R-456"}]}`))}, nil
	})}}
	r, err := DiscoverWorkdayInventory(context.Background(), verified, workday.InventoryConfig{Site: workday.Site{Company: "example", Instance: "wd5", Name: "External"}})
	if err != nil || len(r.URLs) != 2 || r.Truncated || count != 1 {
		t.Fatalf("result=%+v count=%d err=%v", r, count, err)
	}
	if _, err := DiscoverWorkdayInventory(context.Background(), nil, workday.InventoryConfig{}); err == nil {
		t.Fatal("unverified transport accepted")
	}
}

func TestWorkdayDiscoveryPublisherReservationIsNotAnEmptySuccess(t *testing.T) {
	verified := &VerifiedDirectHTTP{client: &http.Client{Transport: richRoundTrip(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Header: http.Header{"Tdm-Reservation": []string{"1"}}, Body: io.NopCloser(strings.NewReader(`{"total":0}`))}, nil
	})}}
	r, err := DiscoverWorkdayInventory(context.Background(), verified, workday.InventoryConfig{Site: workday.Site{Company: "example", Instance: "wd5", Name: "External"}})
	var failure *DiscoveryError
	if !errors.As(err, &failure) || failure.Kind != "publisher_reserved" || len(r.URLs) > 0 {
		t.Fatalf("result=%+v err=%v", r, err)
	}
}

func TestWorkdayDetailUsesSealedNativeClientAndPreservesContent(t *testing.T) {
	count := 0
	verified := &VerifiedDirectHTTP{client: &http.Client{Transport: richRoundTrip(func(r *http.Request) (*http.Response, error) {
		count++
		if r.Method != "GET" || r.URL.String() != "https://example.wd5.myworkdayjobs.com/wday/cxs/example/External/job/Engineer_R-123" {
			t.Fatalf("detail request changed: %s %s", r.Method, r.URL)
		}
		return &http.Response{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(`{"jobPostingInfo":{"title":"Engineer","jobDescription":"<p>Build services.</p>","location":"Zurich","timeType":"Full time","jobReqId":"R-123"}}`))}, nil
	})}}
	result, err := FetchWorkdayDetail(context.Background(), verified, "https://example.wd5.myworkdayjobs.com/en-US/External/job/Engineer_R-123", nil)
	if err != nil || result.Gone || result.Content.Title == nil || *result.Content.Title != "Engineer" || result.Content.Description == nil || *result.Content.Description != "<p>Build services.</p>" || result.Content.Metadata["jobReqId"] != "R-123" || count != 1 {
		t.Fatalf("detail=%+v count=%d err=%v", result, count, err)
	}
	if _, err := FetchWorkdayDetail(context.Background(), nil, "https://example.wd5.myworkdayjobs.com/External/job/Engineer_R-123", nil); err == nil {
		t.Fatal("unverified detail client accepted")
	}
}

func TestWorkdayDetailReserved404CannotBecomeGone(t *testing.T) {
	verified := &VerifiedDirectHTTP{client: &http.Client{Transport: richRoundTrip(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 404, Header: http.Header{"Tdm-Reservation": []string{"1"}}, Body: io.NopCloser(strings.NewReader(`not found`))}, nil
	})}}
	result, err := FetchWorkdayDetail(context.Background(), verified, "https://example.wd5.myworkdayjobs.com/External/job/Engineer_R-123", nil)
	var reservation *workday.ReservationError
	if !errors.As(err, &reservation) || result.Gone {
		t.Fatalf("reserved detail became gone: %+v %v", result, err)
	}
}
