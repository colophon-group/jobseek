package worker

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

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type seekDetailTransport func(*http.Request) (*http.Response, error)

func (f seekDetailTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type seekBrokenBody struct{ io.Reader }

func (b seekBrokenBody) Read(p []byte) (int, error) {
	n, _ := b.Reader.Read(p)
	return n, io.ErrUnexpectedEOF
}
func (b seekBrokenBody) Close() error { return nil }

func TestSeekDetailTransportPolicyAndResponseIdentity(t *testing.T) {
	data, err := os.ReadFile("../api-sniffer-monitor/testdata/python_seek_detail.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct{ Payload json.RawMessage }
	if json.Unmarshal(data, &cases) != nil || len(cases) != 25 {
		t.Fatal("Python detail fixtures")
	}
	for _, mode := range []string{"complete", "wrong_job", "wrong_advertiser", "expired", "missing_job", "404", "503", "malformed", "redirect", "header_reserved", "503_header_reserved", "body_reserved", "503_body_reserved", "broken_body_reserved", "broken_body", "transport"} {
		t.Run(mode, func(t *testing.T) {
			p := queue.WorkdayDetailProfile{Profile: "seek.graphql-detail/v1", SourceURL: "https://au.seek.com/job/94267983", Endpoint: "https://au.seek.com/graphql", HTTPAPIConfig: map[string]any{"advertiser_id": "9094357"}}
			calls, waits := 0, 0
			client := &http.Client{Transport: seekDetailTransport(func(r *http.Request) (*http.Response, error) {
				calls++
				body, _ := io.ReadAll(r.Body)
				var payload map[string]any
				if r.Method != "POST" || r.URL.String() != p.Endpoint || r.Header.Get("Referer") != "https://au.seek.com/" || r.Header.Get("Content-Type") != "application/json" || json.Unmarshal(body, &payload) != nil || payload["operationName"] != "JobDetails" {
					t.Error("unbound GraphQL request")
				}
				if mode == "transport" {
					return nil, errors.New("fixture transport failure")
				}
				status, raw := 200, string(cases[0].Payload)
				headers := http.Header{"Content-Type": {"application/json"}}
				switch mode {
				case "wrong_job":
					raw = strings.ReplaceAll(raw, "94267983", "999")
				case "wrong_advertiser":
					raw = strings.ReplaceAll(raw, "9094357", "999")
				case "expired":
					raw = strings.ReplaceAll(raw, `"isExpired": false`, `"isExpired": true`)
				case "missing_job":
					raw = `{"data":{"jobDetails":{"job":null}}}`
				case "404":
					status = 404
				case "malformed":
					raw = "{"
				case "redirect":
					status = 307
					headers.Set("Location", "https://other.example/graphql")
				}
				if strings.Contains(mode, "503") {
					status = 503
				}
				if strings.Contains(mode, "header_reserved") {
					headers.Set("TDM-Reservation", "1")
					raw = "{invalid"
				}
				if strings.Contains(mode, "body_reserved") {
					raw = `<html><head><meta name="tdm-reservation" content="1"></head></html>`
					headers.Set("Content-Type", "text/html")
				}
				var responseBody io.ReadCloser = io.NopCloser(strings.NewReader(raw))
				if strings.HasPrefix(mode, "broken_body") {
					responseBody = seekBrokenBody{strings.NewReader(raw)}
				}
				return &http.Response{StatusCode: status, Header: headers, Body: responseBody, Request: r}, nil
			})}
			values, reserved, failure := fetchSeekDetail(context.Background(), client, p, func(context.Context, time.Duration) error { waits++; return nil })
			if strings.Contains(mode, "reserved") {
				if reserved == nil || failure != nil || values != nil || calls != 1 || waits != 0 {
					t.Fatal("reservation lost or retried", reserved, failure, calls, waits)
				}
			} else if mode == "complete" {
				if failure != nil || reserved != nil || values["title"] != "Forklift Operator" || values["description"] != "<p>Operate equipment safely.</p>" || calls != 1 {
					t.Fatal(values, reserved, failure)
				}
			} else {
				if failure == nil || reserved != nil || len(values) != 0 {
					t.Fatal("invalid detail succeeded", values, reserved, failure)
				}
				want := 1
				if mode == "503" || mode == "malformed" || mode == "broken_body" || mode == "transport" {
					want = 3
				}
				if calls != want || waits != want-1 {
					t.Fatal("retry contract", calls, waits, want)
				}
			}
		})
	}
}
