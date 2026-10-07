package main

import (
	"encoding/json"
	"errors"
	"fmt"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"net/http"
	"strings"
	"testing"
)

func replayFetchOptions(t *testing.T) api.BrowserReplayOptions {
	t.Helper()
	o, e := api.BrowserReplayOptionsFromMetadata("https://example.com/careers", `{"browser":true,"api_url":"https://example.com/api","method":"POST","json_path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"title"},"pagination":{"param_name":"page","start_value":1,"max_pages":5}}`)
	if e != nil {
		t.Fatal(e)
	}
	return o
}
func TestAPIReplayFetchQuotesPrivateValuesAndKeepsPageOrigin(t *testing.T) {
	o := replayFetchOptions(t)
	r := api.Request{Method: "POST", URL: "https://example.com/api?page=2", Body: `{"label":"');globalThis.injected=true;//"}`, Headers: http.Header{"Authorization": {"Bearer private-token"}, "Host": {"foreign.com"}, "Content-Length": {"999"}}}
	expression, e := replayFetchExpression(o, r)
	if e != nil || !strings.Contains(expression, `"authorization"`) && !strings.Contains(expression, `"Authorization"`) || strings.Contains(expression, "foreign.com") || strings.Contains(expression, "999") {
		t.Fatal("fixed request compiler differs", e)
	}
	start := strings.LastIndex(expression, "})(")
	var compiled map[string]any
	if start < 0 || json.Unmarshal([]byte(expression[start+3:len(expression)-1]), &compiled) != nil || compiled["body"] != r.Body || compiled["url"] != r.URL {
		t.Fatal("private request values changed JavaScript", expression)
	}
	for _, bad := range []api.Request{{Method: "POST", URL: "https://foreign.com/api"}, {Method: "GET", URL: r.URL}, {Method: "POST", URL: r.URL, Headers: http.Header{"Authorization": {"one", "two"}}}, {Method: "POST", URL: r.URL, Headers: http.Header{"Authorization": {"one\nInjected: yes"}}}} {
		if _, e := replayFetchExpression(o, bad); e == nil {
			t.Fatal("invalid replay escaped request binding")
		}
	}
	cleaned := replayHeaders(r.Headers)
	cleaned.Set("Authorization", "changed")
	if cleaned.Get("Host") != "" || r.Headers.Get("Authorization") != "Bearer private-token" {
		t.Fatal("header cleaning changed original lease")
	}
}
func TestAPIReplayFetchPreservesPolicyAndRejectsCredentialReflection(t *testing.T) {
	o := replayFetchOptions(t)
	r := api.Request{Method: "POST", URL: o.Inventory.Endpoint, Headers: http.Header{"Authorization": {"Bearer private-token"}}}
	response := replayFetchResponse{Status: 200, URL: r.URL, Body: `{"jobs":[{"id":1}]}`}
	d, e := replayResponseDocument(o, r, response)
	if e != nil || d == nil {
		t.Fatal("valid replay response rejected", e)
	}
	reservation := "1"
	response.Reservation = &reservation
	_, e = replayResponseDocument(o, r, response)
	var denied *policy.Reservation
	if !errors.As(e, &denied) {
		t.Fatal("publisher denial lost", e)
	}
	response.Reservation = nil
	for _, body := range []string{`{"jobs":[{"title":"private-token"}]}`, `{"jobs":[{"title":"Bearer private-token"}]}`, "invalid JSON"} {
		response.Body = body
		if _, e = replayResponseDocument(o, r, response); e == nil {
			t.Fatal("invalid/private body exported")
		}
	}
	response.Body = `{"jobs":[]}`
	response.Status = 403
	if _, e = replayResponseDocument(o, r, response); e == nil {
		t.Fatal("HTTP failure accepted as empty result")
	}
	response.Status = 200
	response.URL = "https://foreign.com/api"
	if _, e = replayResponseDocument(o, r, response); e == nil {
		t.Fatal("redirect changed request authority")
	}
	if _, e = json.Marshal(response); e == nil || fmt.Sprintf("%+v %#v", response, response) != "private API browser response private API browser response" {
		t.Fatal("private response escaped diagnostics")
	}
}
