package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/network"
)

const dayforceFixtureSearch = "https://jobs.dayforcehcm.com/api/geo/native/jobposting/search"
const dayforceFixtureBody = `{"clientNamespace":"native","jobBoardCode":"CANDIDATEPORTAL","cultureCode":"en-US","distanceUnit":0,"paginationStart":0}`

func TestDayforceSessionHeadersMatchActualPython(t *testing.T) {
	data, err := os.ReadFile("testdata/python_dayforce_headers.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name    string
		Headers network.Headers
		Result  map[string]string
		Error   bool
	}
	if json.Unmarshal(data, &cases) != nil || len(cases) != 12 {
		t.Fatal("missing Python session-header references")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			h, err := dayforceHeaders(c.Headers)
			if (err != nil) != c.Error || !c.Error && h.token != c.Result["x-csrf-token"] {
				t.Fatal("Python token validation differs")
			}
		})
	}
}

func dayforceFixtureRequest(id network.RequestID, frame cdp.FrameID, source, method string, headers network.Headers) *network.EventRequestWillBeSent {
	return &network.EventRequestWillBeSent{RequestID: id, FrameID: frame, Request: &network.Request{URL: source, Method: method, Headers: headers}}
}
func dayforceFixtureResponse(id network.RequestID, frame cdp.FrameID, source string, status int64) *network.EventResponseReceived {
	return &network.EventResponseReceived{RequestID: id, FrameID: frame, Response: &network.Response{URL: source, Status: status}}
}

func TestDayforceSearchCaptureCorrelatesFirstPartyFrameRequestAndFinalStatus(t *testing.T) {
	frame := cdp.FrameID("main")
	capture, err := newDayforceSearchCapture(frame, dayforceFixtureSearch)
	if err != nil {
		t.Fatal(err)
	}
	defer capture.erase()
	token := strings.Repeat("s", 32)
	for _, row := range []struct {
		url, method string
		frame       cdp.FrameID
	}{
		{"https://foreign.invalid/search", "POST", frame},
		{dayforceFixtureSearch, "GET", frame},
		{dayforceFixtureSearch, "POST", "child"},
	} {
		capture.observe(dayforceFixtureRequest("foreign", row.frame, row.url, row.method, network.Headers{"x-csrf-token": token}))
		capture.observe(dayforceFixtureResponse("foreign", row.frame, row.url, 200))
	}
	select {
	case <-capture.ready:
		t.Fatal("foreign traffic supplied session credentials")
	default:
	}
	capture.observe(dayforceFixtureRequest("first", frame, dayforceFixtureSearch, "POST", network.Headers{"X-CSRF-Token": token, "Cookie": "synthetic-private"}))
	capture.observe(dayforceFixtureResponse("unrelated", frame, dayforceFixtureSearch, 200))
	select {
	case <-capture.ready:
		t.Fatal("uncorrelated response supplied session credentials")
	default:
	}
	capture.observe(dayforceFixtureResponse("first", frame, dayforceFixtureSearch, 200))
	h, status, err := capture.wait(context.Background())
	if err != nil || status != 200 || h.token != token {
		t.Fatal("correlated successful POST was not captured")
	}
	capture.observe(dayforceFixtureResponse("first", frame, dayforceFixtureSearch, 403))
	h, status, err = capture.wait(context.Background())
	if err != nil || status != 200 || h.token != token {
		t.Fatal("later event mutated first capture")
	}
	for _, value := range []any{h, capture} {
		if strings.Contains(fmt.Sprintf("%v %+v %#v", value, value, value), token) {
			t.Fatal("formatting leaked session token")
		}
		body, _ := json.Marshal(value)
		if strings.Contains(string(body), token) {
			t.Fatal("JSON leaked session token")
		}
	}
	capture.erase()
	if capture.headers.token != "" || len(capture.pending) != 0 {
		t.Fatal("session retained token after cleanup")
	}
}

func TestDayforceSearchCaptureRefusesDeniedMissingMalformedAndAmbiguousTokens(t *testing.T) {
	token := strings.Repeat("s", 32)
	for _, row := range []struct {
		name    string
		headers network.Headers
		status  int64
		failed  bool
	}{
		{"denied", network.Headers{"x-csrf-token": token}, 403, false},
		{"missing", network.Headers{}, 200, false},
		{"short", network.Headers{"x-csrf-token": "short"}, 200, false},
		{"oversized", network.Headers{"x-csrf-token": strings.Repeat("s", 513)}, 200, false},
		{"non-ascii", network.Headers{"x-csrf-token": strings.Repeat("é", 32)}, 200, false},
		{"non-string", network.Headers{"x-csrf-token": 123}, 200, false},
		{"ambiguous", network.Headers{"X-CSRF-Token": token, "x-csrf-token": token}, 200, false},
		{"network-failed", network.Headers{"x-csrf-token": token}, 0, true},
	} {
		t.Run(row.name, func(t *testing.T) {
			capture, _ := newDayforceSearchCapture("main", dayforceFixtureSearch)
			defer capture.erase()
			capture.observe(dayforceFixtureRequest("first", "main", dayforceFixtureSearch, "POST", row.headers))
			if row.failed {
				capture.observe(&network.EventLoadingFailed{RequestID: "first"})
			} else {
				capture.observe(dayforceFixtureResponse("first", "main", dayforceFixtureSearch, row.status))
			}
			h, _, err := capture.wait(context.Background())
			if err != errDayforceSession || h.token != "" {
				t.Fatal("invalid response became session authority")
			}
		})
	}
}

func TestDayforceSearchExpressionSealsOriginAndRejectsForeignOrAmbiguousBody(t *testing.T) {
	h := dayforceSessionHeaders{token: strings.Repeat("s", 32)}
	expression, err := dayforceSearchExpression(dayforceFixtureSearch, h, []byte(dayforceFixtureBody))
	if err != nil || !strings.Contains(expression, `credentials:"same-origin"`) || !strings.Contains(expression, `redirect:"error"`) || strings.Contains(expression, "Cookie") {
		t.Fatal("compiled browser query changed session scope")
	}
	for _, body := range []string{
		strings.Replace(dayforceFixtureBody, `"native"`, `"foreign"`, 1),
		strings.Replace(dayforceFixtureBody, `"distanceUnit":0`, `"distanceUnit":null`, 1),
		strings.Replace(dayforceFixtureBody, `"paginationStart":0`, `"paginationStart":true`, 1),
		strings.Replace(dayforceFixtureBody, `"paginationStart":0`, `"paginationStart":50000`, 1),
		strings.Replace(dayforceFixtureBody, `"paginationStart":0`, `"paginationStart":-1`, 1),
		strings.Replace(dayforceFixtureBody, `"paginationStart":0`, `"paginationStart":0,"paginationStart":1`, 1),
		strings.Replace(dayforceFixtureBody, `"paginationStart":0`, `"paginationStart":0,"url":"https://foreign.invalid"`, 1),
		`[]`, dayforceFixtureBody + `{}`,
	} {
		if _, err := dayforceSearchExpression(dayforceFixtureSearch, h, []byte(body)); err != errDayforceSession {
			t.Fatal("invalid query body reached expression compiler")
		}
	}
	for _, source := range []string{"http://jobs.dayforcehcm.com/api/geo/native/jobposting/search", "https://jobs.dayforcehcm.com:443/api/geo/native/jobposting/search", "https://jobs.dayforcehcm.com/api/geo/native/jobposting/search?extra=1", "https://jobs.dayforcehcm.com/api/geo/api/jobposting/search"} {
		if _, err := newDayforceSearchCapture("main", source); err != errDayforceSession {
			t.Fatal("unbound search target reached capture")
		}
	}
}
