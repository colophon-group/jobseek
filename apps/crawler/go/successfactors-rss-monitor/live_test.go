package successfactorsrss

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/netip"
	"strings"
	"testing"
	"time"
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

func TestConfiguredCategoryFeedRequest(t *testing.T) {
	feedURL := "https://jobs.example.com/services/rss/category/?catid=2842101"
	summary, err := Fetch(context.Background(), doFunc(func(req *http.Request) (*http.Response, error) {
		if req.URL.String() != feedURL {
			t.Fatalf("changed feed request: %s", req.URL)
		}
		return response(req, 200, `<rss><item><link>https://jobs.example.com/1</link></item></rss>`), nil
	}), feedURL, func(Job) error { return nil })
	if err != nil || summary.Jobs != 1 {
		t.Fatalf("%+v %v", summary, err)
	}
	for _, raw := range []string{
		"https://jobs.example.com/services/rss/category/?catid=2842101&offset=1",
		"https://jobs.example.com/services/rss/category/?catid=abc",
		"https://jobs.example.com/services/rss/category/?catid=0",
		"https://jobs.example.com/googlefeed.xml?locale=en",
		"http://jobs.example.com/googlefeed.xml",
		"https://user@jobs.example.com/googlefeed.xml",
	} {
		if _, err := validFeedURL(raw); err == nil {
			t.Errorf("accepted %s", raw)
		}
	}
}

func TestReadIdleBodyTimesOutAndClosesBlockedRead(t *testing.T) {
	reader, writer := io.Pipe()
	defer writer.Close()
	body := &readIdleBody{ReadCloser: reader, timeout: 20 * time.Millisecond}
	_, err := body.Read(make([]byte, 1))
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("got %v", err)
	}
	if _, err := writer.Write([]byte("x")); err == nil {
		t.Fatal("body remained open")
	}
}

func TestReadIdleBudgetExcludesDownstreamPause(t *testing.T) {
	body := &readIdleBody{ReadCloser: io.NopCloser(strings.NewReader("ab")), timeout: 100 * time.Millisecond}
	one := make([]byte, 1)
	if _, err := body.Read(one); err != nil || string(one) != "a" {
		t.Fatalf("first read %q %v", one, err)
	}
	time.Sleep(150 * time.Millisecond)
	if _, err := body.Read(one); err != nil || string(one) != "b" {
		t.Fatalf("after pause %q %v", one, err)
	}
	client := newClient()
	defer client.CloseIdleConnections()
	if client.Timeout != 0 {
		t.Fatalf("whole-response timeout still active: %s", client.Timeout)
	}
}

type failedBody struct{ io.Reader }

func (b failedBody) Close() error { return nil }
func (b failedBody) Read(p []byte) (int, error) {
	n, err := b.Reader.Read(p)
	if err == io.EOF {
		return n, io.ErrUnexpectedEOF
	}
	return n, err
}

func TestTransportRetryBoundaryBeforeFirstRawItem(t *testing.T) {
	for _, prefix := range []string{"", `<?xml version="1.0"?><rss>`, `<rss><item><title>No link</title></item>`} {
		attempts := 0
		result, err := Fetch(context.Background(), doFunc(func(req *http.Request) (*http.Response, error) {
			attempts++
			if attempts == 1 {
				r := response(req, 200, "")
				r.Body = failedBody{strings.NewReader(prefix)}
				return r, nil
			}
			return response(req, 200, `<rss><item><link>https://jobs.example.com/1</link></item></rss>`), nil
		}), "https://jobs.example.com/googlefeed.xml", func(Job) error { return nil })
		if strings.Contains(prefix, "<item>") {
			if err == nil || attempts != 1 {
				t.Fatalf("replayed partial feed: %+v %v", result, err)
			}
		} else if err != nil || attempts != 2 || result.Jobs != 1 {
			t.Fatalf("did not retry before first item: %+v %v", result, err)
		}
	}
}
