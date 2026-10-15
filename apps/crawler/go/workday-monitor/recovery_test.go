package workday

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/cookiejar"
	"strings"
	"testing"
)

type recoveryRoundTrip func(*http.Request) (*http.Response, error)

func (f recoveryRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestRecoveryConfigRetainsExactOriginalBoundary(t *testing.T) {
	valid := map[string]any{"company": "fixture", "wd_instance": "wd5", "site": "External", "proxy": true}
	got, err := ParseRecoveryConfig(valid)
	if err != nil || got == nil || !got.Proxy {
		t.Fatal(got, err)
	}
	for _, change := range []map[string]any{{"extra": true}, {"company": "user@host"}, {"company": "a_b"}, {"wd_instance": "wdx"}, {"site": "../site"}, {"proxy": "true"}} {
		c := map[string]any{}
		for k, v := range valid {
			c[k] = v
		}
		for k, v := range change {
			c[k] = v
		}
		if _, err := ParseRecoveryConfig(c); err == nil {
			t.Fatal("invalid configured recovery admitted")
		}
	}
	if c, err := ParseRecoveryConfig(nil); err != nil || c != nil {
		t.Fatal("absent recovery changed")
	}
}

func TestRecoverySearchRequiresExactlyOneRequisitionPath(t *testing.T) {
	for _, mode := range []string{"exact", "duplicate", "ambiguous", "wrong-id", "malformed-rows", "missing-rows", "no-id", "empty-title", "publisher"} {
		t.Run(mode, func(t *testing.T) {
			calls := 0
			jar, _ := cookiejar.New(nil)
			client := &http.Client{Jar: jar, Transport: recoveryRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				header := http.Header{"Content-Type": []string{"application/json"}}
				body := ""
				if calls == 1 {
					if r.Method != "POST" || r.URL.String() != "https://fixture.wd5.myworkdayjobs.com/wday/cxs/fixture/External/jobs" {
						t.Fatal("search endpoint changed")
					}
					var request map[string]any
					if json.NewDecoder(r.Body).Decode(&request) != nil || request["searchText"] != "REQ100" || request["limit"] != float64(20) || request["offset"] != float64(0) || len(request) != 4 {
						t.Fatal("bounded exact requisition search changed")
					}
					header.Set("Set-Cookie", "session=fixture; Path=/")
					switch mode {
					case "ambiguous":
						body = `{"jobPostings":[{"externalPath":"/job/NY/Engineer_REQ100"},{"externalPath":"/job/LA/Engineer_REQ100"}]}`
					case "wrong-id":
						body = `{"jobPostings":[{"externalPath":"/job/NY/Engineer_REQ101"}]}`
					case "malformed-rows":
						body = `{"jobPostings":{}}`
					case "missing-rows":
						body = `{}`
					case "duplicate":
						body = `{"jobPostings":[{"externalPath":"/job/NY/Engineer_REQ100"},{"externalPath":"/job/NY/Engineer_REQ100"}]}`
					default:
						body = `{"jobPostings":[null,5,{"externalPath":"/job/NY/Engineer_REQ100"}]}`
					}
					if mode == "publisher" {
						header.Set("TDM-Reservation", "1")
					}
				} else {
					if r.Method != "GET" || r.URL.String() != "https://fixture.wd5.myworkdayjobs.com/wday/cxs/fixture/External/job/NY/Engineer_REQ100" {
						t.Fatal("recovered detail differs")
					}
					if !strings.Contains(r.Header.Get("Cookie"), "session=fixture") {
						t.Fatal("search/detail cookie session lost")
					}
					body = `{"jobPostingInfo":{"title":"Engineer","jobDescription":"<p>Build systems.</p>"}}`
					if mode == "empty-title" {
						body = `{"jobPostingInfo":{"title":"","jobDescription":"<p>Build systems.</p>"}}`
					}
				}
				return &http.Response{StatusCode: 200, Header: header, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})}
			source := "https://careers.phenom.example/job/REQ100/engineer"
			if mode == "no-id" {
				source = "https://careers.phenom.example/careers"
			}
			got, err := RecoverPhenomDetailWithClient(context.Background(), source, RecoveryConfig{"fixture", "wd5", "External", false}, client)
			if mode == "publisher" {
				if err == nil || calls != 1 {
					t.Fatal("publisher reservation lost", err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			success := mode == "exact" || mode == "duplicate"
			if got.Found != success {
				t.Fatal("wrong or ambiguous requisition selected", got.Found)
			}
			expected := 1
			if success || mode == "empty-title" {
				expected = 2
			}
			if mode == "no-id" {
				expected = 0
			}
			if calls != expected || got.Requests != expected || got.Responses != expected {
				t.Fatal("recovery request boundary changed", calls, got.Requests)
			}
			if success && (got.Detail.Content.Title == nil || *got.Detail.Content.Title != "Engineer") {
				t.Fatal("detail not projected")
			}
		})
	}
}
