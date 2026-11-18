package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"regexp"
	"strings"
	"sync"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/network"
	"github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/chromedp"
)

var errDayforceSession = errors.New("dayforce browser session rejected")
var dayforceTokenPattern = regexp.MustCompile(`^[A-Za-z0-9._~-]{32,512}$`)
var dayforceTenantPattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
var dayforcePortalPattern = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9-]{0,126}[A-Za-z0-9])?$`)
var dayforceCulturePattern = regexp.MustCompile(`^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})+$`)

// This controller-local lease is never a task/result/artifact value. In
// particular, formatting or JSON encoding cannot export the captured token.
type dayforceSessionHeaders struct{ token string }

func (dayforceSessionHeaders) String() string   { return "dayforce session headers" }
func (dayforceSessionHeaders) GoString() string { return "dayforce session headers" }

func dayforceHeaders(headers network.Headers) (dayforceSessionHeaders, error) {
	var token string
	count := 0
	for name, value := range headers {
		if !strings.EqualFold(name, "x-csrf-token") {
			continue
		}
		count++
		text, ok := value.(string)
		if !ok || !dayforceTokenPattern.MatchString(text) {
			return dayforceSessionHeaders{}, errDayforceSession
		}
		token = text
	}
	if count != 1 {
		return dayforceSessionHeaders{}, errDayforceSession
	}
	return dayforceSessionHeaders{token: token}, nil
}

func dayforceSearchTenant(source string) (string, error) {
	u, err := url.Parse(source)
	if err != nil || u.Scheme != "https" || u.Host != "jobs.dayforcehcm.com" || u.User != nil || u.Opaque != "" || u.RawQuery != "" || u.Fragment != "" || u.RawPath != "" {
		return "", errDayforceSession
	}
	parts := strings.Split(u.Path, "/")
	if len(parts) != 6 || parts[0] != "" || parts[1] != "api" || parts[2] != "geo" || parts[4] != "jobposting" || parts[5] != "search" || !dayforceTenantPattern.MatchString(parts[3]) {
		return "", errDayforceSession
	}
	switch parts[3] {
	case "api", "app", "help", "support", "www":
		return "", errDayforceSession
	}
	return parts[3], nil
}

type dayforceSearchCapture struct {
	mu      sync.Mutex
	frame   cdp.FrameID
	source  string
	pending map[network.RequestID]dayforceSessionHeaders
	invalid map[network.RequestID]bool
	ready   chan struct{}
	settled bool
	headers dayforceSessionHeaders
	status  int64
	err     error
}

func (*dayforceSearchCapture) String() string   { return "dayforce search capture" }
func (*dayforceSearchCapture) GoString() string { return "dayforce search capture" }

func validDayforceSearchBody(source string, body []byte) bool {
	tenant, err := dayforceSearchTenant(source)
	if err != nil || len(body) == 0 || len(body) > 8192 {
		return false
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	start, err := decoder.Token()
	if err != nil || start != json.Delim('{') {
		return false
	}
	allowed := map[string]bool{"clientNamespace": true, "jobBoardCode": true, "cultureCode": true, "distanceUnit": true, "paginationStart": true}
	fields := map[string]json.RawMessage{}
	for decoder.More() {
		key, err := decoder.Token()
		name, ok := key.(string)
		if err != nil || !ok || !allowed[name] || fields[name] != nil {
			return false
		}
		var value json.RawMessage
		if decoder.Decode(&value) != nil {
			return false
		}
		if bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return false
		}
		fields[name] = value
	}
	end, err := decoder.Token()
	if err != nil || end != json.Delim('}') || len(fields) != 5 || decoder.Decode(new(any)) != io.EOF {
		return false
	}
	var ns, portal, culture string
	var distance, offset int
	return json.Unmarshal(fields["clientNamespace"], &ns) == nil && ns == tenant && json.Unmarshal(fields["jobBoardCode"], &portal) == nil && dayforcePortalPattern.MatchString(portal) && json.Unmarshal(fields["cultureCode"], &culture) == nil && len(culture) <= 32 && dayforceCulturePattern.MatchString(culture) && json.Unmarshal(fields["distanceUnit"], &distance) == nil && distance == 0 && json.Unmarshal(fields["paginationStart"], &offset) == nil && offset >= 0 && offset < 50000
}

func newDayforceSearchCapture(frame cdp.FrameID, source string) (*dayforceSearchCapture, error) {
	if _, err := dayforceSearchTenant(source); err != nil || frame == "" {
		return nil, errDayforceSession
	}
	return &dayforceSearchCapture{frame: frame, source: source, pending: map[network.RequestID]dayforceSessionHeaders{}, invalid: map[network.RequestID]bool{}, ready: make(chan struct{})}, nil
}
func (c *dayforceSearchCapture) finish(headers dayforceSessionHeaders, status int64, err error) {
	if c.settled {
		return
	}
	c.headers, c.status, c.err, c.settled = headers, status, err, true
	clear(c.pending)
	clear(c.invalid)
	close(c.ready)
}

// ListenTarget invokes this without network calls. Only a correlated first
// POST/response in the navigated frame can release a lease; redirect/foreign
// traffic cannot supply credentials. Request tokens remain in bounded memory.
func (c *dayforceSearchCapture) observe(event any) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.settled {
		return
	}
	switch value := event.(type) {
	case *network.EventRequestWillBeSent:
		if value.Request == nil || value.RequestID == "" || value.FrameID != c.frame || value.Request.Method != "POST" || value.Request.URL != c.source {
			return
		}
		if len(c.pending)+len(c.invalid) >= 8 {
			c.finish(dayforceSessionHeaders{}, 0, errDayforceSession)
			return
		}
		headers, err := dayforceHeaders(value.Request.Headers)
		if err != nil {
			c.invalid[value.RequestID] = true
		} else {
			c.pending[value.RequestID] = headers
		}
	case *network.EventResponseReceived:
		if value.Response == nil || value.Response.URL != c.source || value.FrameID != c.frame {
			return
		}
		headers, known := c.pending[value.RequestID]
		if !known && !c.invalid[value.RequestID] {
			return
		}
		if !known || value.Response.Status != 200 {
			c.finish(dayforceSessionHeaders{}, value.Response.Status, errDayforceSession)
			return
		}
		c.finish(headers, 200, nil)
	case *network.EventLoadingFailed:
		if _, known := c.pending[value.RequestID]; known || c.invalid[value.RequestID] {
			c.finish(dayforceSessionHeaders{}, 0, errDayforceSession)
		}
	}
}
func (c *dayforceSearchCapture) wait(ctx context.Context) (dayforceSessionHeaders, int64, error) {
	select {
	case <-ctx.Done():
		return dayforceSessionHeaders{}, 0, ctx.Err()
	case <-c.ready:
		c.mu.Lock()
		defer c.mu.Unlock()
		return c.headers, c.status, c.err
	}
}

func (c *dayforceSearchCapture) erase() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.settled {
		c.finish(dayforceSessionHeaders{}, 0, errDayforceSession)
	}
	c.headers = dayforceSessionHeaders{}
	clear(c.pending)
	clear(c.invalid)
}

// This is the fixed first-party browser query used by the existing Python
// monitor. Callers provide a compiled search body, never JavaScript. The token
// is inserted only in this controller's memory and no headers are returned.
func dayforceSearchExpression(source string, headers dayforceSessionHeaders, body []byte) (string, error) {
	if !validDayforceSearchBody(source, body) || !dayforceTokenPattern.MatchString(headers.token) {
		return "", errDayforceSession
	}
	target, _ := json.Marshal(source)
	token, _ := json.Marshal(headers.token)
	encodedBody, _ := json.Marshal(string(body))
	return `(async()=>{const r=await fetch(` + string(target) + `,{method:"POST",credentials:"same-origin",redirect:"error",headers:{"accept":"application/json","content-type":"application/json","x-csrf-token":` + string(token) + `},body:` + string(encodedBody) + `});const body=await r.text();if(body.length>1048576)throw new Error("dayforce response limit");return {status:r.status,url:r.url,reservation:r.headers.get("tdm-reservation"),policy:r.headers.get("tdm-policy"),body};})()`, nil
}

type dayforceSearchResponse struct {
	Status      int     `json:"status"`
	URL         string  `json:"url"`
	Reservation *string `json:"reservation"`
	Policy      *string `json:"policy"`
	Body        string  `json:"body"`
}

func fetchDayforceInSession(ctx context.Context, source string, headers dayforceSessionHeaders, body []byte) (dayforceSearchResponse, error) {
	var response dayforceSearchResponse
	expression, err := dayforceSearchExpression(source, headers, body)
	if err != nil {
		return response, err
	}
	err = chromedp.Run(ctx, chromedp.ActionFunc(func(actionCtx context.Context) error {
		object, exception, err := runtime.Evaluate(expression).WithReturnByValue(true).WithAwaitPromise(true).Do(actionCtx)
		if err != nil {
			return errDayforceSession
		}
		// Exception details can contain the expression and its private token.
		if exception != nil || object == nil || len(object.Value) > 2<<20 {
			return errDayforceSession
		}
		if json.Unmarshal(object.Value, &response) != nil || response.Status < 100 || response.Status > 599 || response.URL != source || len(response.Body) > 1<<20 {
			return errDayforceSession
		}
		return nil
	}))
	if ctx.Err() != nil {
		return dayforceSearchResponse{}, ctx.Err()
	}
	return response, err
}
