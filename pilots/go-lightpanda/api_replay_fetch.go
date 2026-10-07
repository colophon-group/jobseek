package main

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"

	"github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/chromedp"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type replayFetchResponse struct {
	Status      int     `json:"status"`
	URL         string  `json:"url"`
	Body        string  `json:"body"`
	Reservation *string `json:"reservation"`
	Policy      *string `json:"policy"`
}

func (replayFetchResponse) String() string               { return "private API browser response" }
func (replayFetchResponse) GoString() string             { return "private API browser response" }
func (replayFetchResponse) MarshalJSON() ([]byte, error) { return nil, errReplayCapture }

// The fixed function executes only on the existing fresh target; cookies and
// refreshed request headers stay in that target's controller. JSON quoting
// keeps header/body values from changing the JavaScript program.
func replayFetchExpression(o api.BrowserReplayOptions, r api.Request) (string, error) {
	if r.Method != o.Inventory.Method || !o.Inventory.ResourceMatches(r.URL) || len(r.Body) > 64<<10 {
		return "", errReplayCapture
	}
	headers := map[string]string{}
	size := 0
	for key, values := range r.Headers {
		switch strings.ToLower(key) {
		case "host", "connection", "content-length", "accept-encoding", "transfer-encoding":
			continue
		}
		if len(values) != 1 || len(key) > 256 || len(values[0]) > 8192 || strings.ContainsAny(key+values[0], "\x00\r\n") {
			return "", errReplayCapture
		}
		size += len(key) + len(values[0])
		if size > 64<<10 {
			return "", errResourceLimit
		}
		headers[key] = values[0]
	}
	params, e := json.Marshal(struct {
		Method  string            `json:"method"`
		URL     string            `json:"url"`
		Headers map[string]string `json:"headers"`
		Body    string            `json:"body"`
	}{r.Method, r.URL, headers, r.Body})
	if e != nil {
		return "", errReplayCapture
	}
	return `(async(p)=>{const opts={method:p.method,headers:p.headers};if(p.body)opts.body=p.body;const r=await fetch(p.url,opts);const body=await r.text();if(body.length>2000000)throw new Error("API response limit");return {status:r.status,url:r.url,body,reservation:r.headers.get("tdm-reservation"),policy:r.headers.get("tdm-policy")};})(` + string(params) + `)`, nil
}
func replayResponseDocument(o api.BrowserReplayOptions, r api.Request, response replayFetchResponse) (*api.Document, error) {
	if response.Status < 200 || response.Status >= 300 || !o.Inventory.ResourceMatches(response.URL) || len(response.Body) > replayCaptureBodyLimit {
		return nil, errReplayCapture
	}
	signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: response.Reservation, TdmPolicyHeader: response.Policy}
	if e := policy.Check(signals, response.Body, response.URL); e != nil {
		return nil, e
	}
	for name, values := range r.Headers {
		name = strings.ToLower(name)
		if name != "authorization" && name != "cookie" && !strings.Contains(name, "token") && !strings.Contains(name, "csrf") && !strings.Contains(name, "api-key") {
			continue
		}
		for _, value := range values {
			pieces := []string{value}
			if name == "authorization" {
				if fields := strings.Fields(value); len(fields) == 2 {
					pieces = append(pieces, fields[1])
				}
			}
			for _, secret := range pieces {
				if secret != "" && (strings.Contains(response.Body, secret) || response.Reservation != nil && strings.Contains(*response.Reservation, secret) || response.Policy != nil && strings.Contains(*response.Policy, secret)) {
					return nil, errReplayCapture
				}
			}
		}
	}
	document, e := api.Decode([]byte(response.Body))
	if e != nil {
		return nil, errReplayCapture
	}
	return document, nil
}
func fetchReplayInSession(ctx context.Context, o api.BrowserReplayOptions, r api.Request) (*api.Document, error) {
	expression, e := replayFetchExpression(o, r)
	if e != nil {
		return nil, e
	}
	var response replayFetchResponse
	e = chromedp.Run(ctx, chromedp.ActionFunc(func(call context.Context) error {
		object, exception, err := runtime.Evaluate(expression).WithReturnByValue(true).WithAwaitPromise(true).Do(call)
		// CDP exceptions can echo private expressions. Only fixed owned errors cross
		// this boundary; the containing session disables raw CDP diagnostics.
		if err != nil || exception != nil || object == nil || len(object.Value) > 4*replayCaptureBodyLimit {
			return errReplayCapture
		}
		if json.Unmarshal(object.Value, &response) != nil {
			return errReplayCapture
		}
		return nil
	}))
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if e != nil {
		return nil, e
	}
	return replayResponseDocument(o, r, response)
}

// Use the same cleaning on initial and later-page requests after selection.
func replayHeaders(headers http.Header) http.Header {
	out := headers.Clone()
	for key := range out {
		switch strings.ToLower(key) {
		case "host", "connection", "content-length", "accept-encoding", "transfer-encoding":
			delete(out, key)
		}
	}
	return out
}
