package dom

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/cookiejar"
	"strings"
	"testing"
)

type directRoundTrip func(*http.Request) (*http.Response, error)

func (f directRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func directConfig(t *testing.T, extra string) Object {
	t.Helper()
	var config Object
	d := json.NewDecoder(strings.NewReader(`{"steps":[{"tag":"h1","field":"title"},{"tag":"h2","text":"Role","offset":1,"field":"description","html":true}]` + extra + `}`))
	d.UseNumber()
	if err := d.Decode(&config); err != nil {
		t.Fatal(err)
	}
	return config
}

func TestDirectDOMTransportRetainsRetriesCookiesAndPublicHeaderContract(t *testing.T) {
	for _, mode := range []string{"retry", "public-headers", "cookie-redirect"} {
		t.Run(mode, func(t *testing.T) {
			config := directConfig(t, `,"retry_statuses":{"429":1},"same_origin_redirects":true`)
			if mode == "public-headers" {
				config["request_headers"] = Object{" User-Agent ": " PublicJobs ", "Accept-Language": "en"}
			}
			calls := 0
			jar, _ := cookiejar.New(nil)
			client := &http.Client{Jar: jar, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
			client.Transport = directRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				status, body := 200, `<h1>Engineer</h1><h2>Role</h2><p>Build useful products.</p>`
				header := http.Header{"Content-Type": []string{"text/html; charset=utf-8"}}
				if mode == "public-headers" && (r.Header.Get("User-Agent") != "PublicJobs" || r.Header.Get("Accept-Language") != "en") {
					t.Fatal("public headers changed")
				}
				if calls == 1 {
					if mode == "cookie-redirect" {
						status = 302
						header.Set("Location", "/job")
						header.Set("Set-Cookie", "consent=1; Path=/")
					} else {
						status = 429
					}
				} else if mode == "cookie-redirect" && !strings.Contains(r.Header.Get("Cookie"), "consent=1") {
					t.Fatal("cookie handshake lost")
				}
				return &http.Response{StatusCode: status, Header: header, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})
			result, err := FetchDetailWithClient(context.Background(), "https://example.com/job", config, client)
			if mode == "public-headers" {
				if err == nil || result.Status != 429 || calls != 1 {
					t.Fatal("public header status was retried", result, err)
				}
			} else if err != nil || calls != 2 || result.Requests != 2 || result.Content["title"] != "Engineer" || !strings.Contains(result.Content["description"].(string), "Build useful") {
				t.Fatal("DOM transport or content changed", result, err)
			}
		})
	}
}

func TestDirectDOMPolicyEncodingBoundsAndCanonicalURLDefaults(t *testing.T) {
	for _, mode := range []string{"latin1", "large-document", "gone-before-status", "reserved-redirect", "header-before-status", "meta-before-status", "foreign-redirect", "invalid-gone-regex"} {
		t.Run(mode, func(t *testing.T) {
			config := directConfig(t, `,"gone_url_pattern":"/Error$","defaults_by_url":{"https://example.com/job":{"location":"Zurich"}}`)
			calls := 0
			client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }, Transport: directRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				status, body := 200, `<h1>Engineer</h1><h2>Role</h2><p>Build useful products.</p>`
				header := http.Header{"Content-Type": []string{"text/html; charset=utf-8"}}
				switch mode {
				case "latin1":
					body = "<h1>Ing\xe9nieur</h1><h2>Role</h2><p>Work</p>"
				case "large-document":
					body += "<!--" + strings.Repeat("x", 2<<20) + "-->"
				case "gone-before-status":
					if calls == 1 {
						status = 302
						header.Set("Location", "/Error")
					} else {
						status = 503
					}
				case "reserved-redirect":
					if calls == 1 {
						status = 302
						header.Set("Location", "/reserved")
					} else {
						header.Set("TDM-Reservation", "1")
						header.Set("TDM-Policy", "https://example.com/policy")
					}
				case "header-before-status":
					status = 410
					body = ""
					header.Set("TDM-Reservation", "1")
				case "meta-before-status":
					status = 503
					body = `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="https://example.com/policy">`
				case "foreign-redirect":
					status = 302
					header.Set("Location", "https://other.example/job")
				}
				return &http.Response{StatusCode: status, Header: header, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})}
			if mode == "latin1" {
				config["encoding"] = "latin-1"
			}
			if mode == "invalid-gone-regex" {
				config["gone_url_pattern"] = "["
			}
			if mode == "foreign-redirect" {
				config["same_origin_redirects"] = true
			}
			result, err := FetchDetailWithClient(context.Background(), "https://example.com/job", config, client)
			switch mode {
			case "header-before-status", "meta-before-status", "reserved-redirect":
				if err == nil || result.ErrorKind != "tdm" || result.Content != nil {
					t.Fatal("publisher reservation lost", result, err)
				}
				if mode == "reserved-redirect" && (result.FinalURL != "https://example.com/reserved" || result.TDMSource != "header" || result.TDMPolicy != "https://example.com/policy") {
					t.Fatal("actual reservation resource lost", result)
				}
			case "gone-before-status":
				if err == nil || result.Status != 410 || result.ErrorKind != "status" {
					t.Fatal("gone redirect lost", result, err)
				}
			case "foreign-redirect":
				if err == nil || calls != 1 {
					t.Fatal("same origin barrier bypassed", result, err)
				}
			default:
				if err != nil || result.Content["locations"].([]string)[0] != "Zurich" {
					t.Fatal("canonical URL defaults lost", result, err)
				}
				if mode == "latin1" && result.Content["title"] != "Ingénieur" {
					t.Fatal("configured encoding lost", result)
				}
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	client := &http.Client{Transport: directRoundTrip(func(r *http.Request) (*http.Response, error) { return nil, r.Context().Err() })}
	if _, err := FetchDetailWithClient(ctx, "https://example.com/job", directConfig(t, ""), client); !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled request lost", err)
	}
}
