package successfactorsrss

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/netip"
	"strings"
	"testing"
)

type doFunc func(*http.Request) (*http.Response, error)

func (fn doFunc) Do(request *http.Request) (*http.Response, error) { return fn(request) }

func response(request *http.Request, status int, body string) *http.Response {
	return &http.Response{
		StatusCode: status, Request: request,
		Header: make(http.Header), Body: io.NopCloser(strings.NewReader(body)),
	}
}

func TestFetchStreamsJobsAndRetriesStatus(t *testing.T) {
	requests := 0
	emitted := []Job{}
	feed := `<rss><item><link>https://jobs.example.com/1</link><title>Engineer</title></item></rss>`
	summary, err := Fetch(context.Background(), doFunc(func(req *http.Request) (*http.Response, error) {
		requests++
		if requests == 1 {
			return response(req, 429, ""), nil
		}
		return response(req, 200, feed), nil
	}), "https://jobs.example.com/googlefeed.xml", func(job Job) error {
		emitted = append(emitted, job)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if requests != 2 || summary.Requests != 2 || summary.Responses != 2 || summary.Jobs != 1 || summary.Items != 1 || summary.Bytes != int64(len(feed)) || len(emitted) != 1 || emitted[0].URL != "https://jobs.example.com/1" {
		t.Fatalf("summary=%+v emitted=%+v", summary, emitted)
	}
}

func TestFetchFailsAfterPartialFeed(t *testing.T) {
	emitted := 0
	feed := `<rss><item><link>https://jobs.example.com/1</link></item><item>`
	summary, err := Fetch(context.Background(), doFunc(func(req *http.Request) (*http.Response, error) {
		return response(req, 200, feed), nil
	}), "https://jobs.example.com/googlefeed.xml", func(Job) error {
		emitted++
		return nil
	})
	if err == nil || emitted != 1 || summary.Jobs != 1 || summary.Truncated {
		t.Fatalf("partial feed incorrectly succeeded: summary=%+v emitted=%d err=%v", summary, emitted, err)
	}
}

func TestFetchRejectsReservedTDMAndUnsafeAddresses(t *testing.T) {
	summary, err := Fetch(context.Background(), doFunc(func(req *http.Request) (*http.Response, error) {
		resp := response(req, 200, `<rss/>`)
		resp.Header.Set("TDM-Reservation", "1")
		return resp, nil
	}), "https://jobs.example.com/googlefeed.xml", func(Job) error { return errors.New("unexpected job") })
	if err == nil || summary.ErrorKind != "tdm" {
		t.Fatalf("summary=%+v err=%v", summary, err)
	}
	for _, raw := range []string{"127.0.0.1", "10.0.0.1", "192.0.2.1", "2001:db8::1"} {
		if publicAddress(netip.MustParseAddr(raw)) {
			t.Errorf("accepted unsafe address %s", raw)
		}
	}
}
