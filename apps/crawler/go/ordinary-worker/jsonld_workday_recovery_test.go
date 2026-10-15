package worker

import (
	"context"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
	"io"
	"net/http"
	"strings"
	"testing"
)

type recoveryWorkerRoundTrip func(*http.Request) (*http.Response, error)

func (f recoveryWorkerRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestJSONLDWorkdayRecoveryPreservesPrimaryStatusPolicyAndTransport(t *testing.T) {
	for _, mode := range []string{"recover", "primary-success", "missing", "publisher-primary", "publisher-search", "wrong-id", "fallback-direct", "search-http-status"} {
		t.Run(mode, func(t *testing.T) {
			primaryCalls, fallbackCalls := 0, 0
			primary := &http.Client{Transport: recoveryWorkerRoundTrip(func(r *http.Request) (*http.Response, error) {
				primaryCalls++
				status := 403
				body := "blocked"
				header := http.Header{"Content-Type": []string{"text/html"}}
				if mode == "primary-success" {
					status = 200
					body = nativeJSONLDHTML
				}
				if mode == "publisher-primary" {
					header.Set("TDM-Reservation", "1")
				}
				return &http.Response{StatusCode: status, Header: header, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})}
			fallback := &VerifiedHTTP{proxyRequired: mode != "fallback-direct", client: &http.Client{Transport: recoveryWorkerRoundTrip(func(r *http.Request) (*http.Response, error) {
				fallbackCalls++
				header := http.Header{"Content-Type": []string{"application/json"}}
				body := `{"jobPostings":[{"externalPath":"/job/NY/Engineer_REQ100"}]}`
				if mode == "wrong-id" {
					body = `{"jobPostings":[{"externalPath":"/job/NY/Engineer_REQ101"}]}`
				}
				if r.Method == "GET" {
					body = `{"jobPostingInfo":{"title":"Recovered Engineer","jobDescription":"<p>Complete role.</p>"}}`
				}
				if mode == "publisher-search" {
					header.Set("TDM-Reservation", "1")
					header.Set("TDM-Policy", "https://fixture.wd5.myworkdayjobs.com/policy")
				}
				if mode == "search-http-status" {
					return &http.Response{StatusCode: 404, Header: header, Body: io.NopCloser(strings.NewReader("missing")), Request: r}, nil
				}
				return &http.Response{StatusCode: 200, Header: header, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})}}
			verified := &VerifiedHTTP{client: primary, workdayRecoveryProxy: fallback}
			if mode == "missing" {
				verified.workdayRecoveryProxy = nil
			}
			if mode == "fallback-direct" {
				verified = &VerifiedHTTP{client: primary, proxyRequired: true, workdayRecoveryDirect: fallback}
			}
			profile := queue.WorkdayDetailProfile{SourceURL: "https://careers.example.com/job/REQ100/engineer", JSONLDConfig: map[string]any{}, JSONLDRecovery: &workday.RecoveryConfig{Company: "fixture", Instance: "wd5", Site: "External", Proxy: mode != "fallback-direct"}}
			result, err := fetchJSONLDWithWorkdayRecovery(context.Background(), verified, primary, profile)
			switch mode {
			case "search-http-status":
				if err == nil || result.ErrorKind != "status" || result.Status != 404 || fallbackCalls != 1 {
					t.Fatal("secondary HTTP status classification lost", err)
				}
			case "primary-success":
				if err != nil || primaryCalls != 1 || fallbackCalls != 0 {
					t.Fatal("successful primary changed", err)
				}
			case "publisher-primary":
				if err == nil || result.ErrorKind != "tdm" || fallbackCalls != 0 || primaryCalls != 1 {
					t.Fatal("reserved primary was recovered", err)
				}
			case "publisher-search":
				if err == nil || result.ErrorKind != "tdm" || !strings.Contains(result.FinalURL, "myworkdayjobs.com") || fallbackCalls != 1 {
					t.Fatal("fallback policy attribution lost", err)
				}
			case "missing":
				if err != queue.ErrConfiguration || fallbackCalls != 0 {
					t.Fatal("missing protected proxy fell back silently", err)
				}
			case "wrong-id":
				if err == nil || result.Status != 403 || fallbackCalls != 1 {
					t.Fatal("wrong identity recovered", err)
				}
			default:
				if err != nil || result.Content["title"] != "Recovered Engineer" || primaryCalls != 2 || fallbackCalls != 2 || result.Requests != 4 || result.Status != 200 {
					t.Fatal("recovery or transport boundary changed", err, primaryCalls, fallbackCalls)
				}
			}
		})
	}
}
