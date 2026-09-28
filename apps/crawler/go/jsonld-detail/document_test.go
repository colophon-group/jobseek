package jsonld

import (
	"context"
	"errors"
	"net/http"
	"net/http/cookiejar"
	"strings"
	"testing"
)

func TestDocumentStatusRetryContracts(t *testing.T) {
	for _, c := range []struct {
		name     string
		statuses []int
		opts     DocumentOptions
	}{
		{"ordinary403", []int{403}, DocumentOptions{}},
		{"ordinary406", []int{406}, DocumentOptions{}},
		{"configured406", []int{406, 406, 200}, DocumentOptions{RetryLimits: map[int]int{406: 2}}},
		{"configured429", []int{429, 429, 429, 200}, DocumentOptions{RetryLimits: map[int]int{429: 3}}},
		{"exhausted429", []int{429, 429, 429}, DocumentOptions{RetryLimits: map[int]int{429: 2}}},
		{"public headers skip retry", []int{429}, DocumentOptions{Headers: map[string]string{"User-Agent": "jobseek"}, PublicHeaders: true, RetryLimits: map[int]int{429: 3}}},
	} {
		t.Run(c.name, func(t *testing.T) {
			calls := 0
			result, err := fetchDocument(context.Background(), doerFunc(func(r *http.Request) (*http.Response, error) {
				response := reply(r, c.statuses[calls], "document")
				calls++
				return response, nil
			}), "https://example.com/job", c.opts, noWait)
			if err != nil || calls != len(c.statuses) || result.Requests != calls || result.Responses != calls || string(result.Body) != "document" {
				t.Fatalf("calls=%d result=%+v err=%v", calls, result, err)
			}
		})
	}
	calls := 0
	_, err := fetchDocument(context.Background(), doerFunc(func(*http.Request) (*http.Response, error) { calls++; return nil, errors.New("transport") }), "https://example.com/job", DocumentOptions{RetryLimits: map[int]int{503: 3}}, noWait)
	if err == nil || calls != 1 {
		t.Fatal("transport failure retried")
	}
}

func TestDocumentPolicyBeforeRetryAndParsing(t *testing.T) {
	for _, source := range []string{"header", "meta"} {
		t.Run(source, func(t *testing.T) {
			calls := 0
			result, err := fetchDocument(context.Background(), doerFunc(func(r *http.Request) (*http.Response, error) {
				calls++
				response := reply(r, 429, `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="license">`)
				if source == "header" {
					response.Header.Set("TDM-Reservation", "1")
					response.Header.Set("TDM-Policy", "license")
				}
				return response, nil
			}), "https://example.com/job", DocumentOptions{RetryLimits: map[int]int{429: 3}}, noWait)
			if err == nil || calls != 1 || result.ErrorKind != "tdm" || result.TDMSource != source || result.TDMPolicy != "license" {
				t.Fatalf("%+v %v", result, err)
			}
		})
	}
}

func TestDocumentRedirectContracts(t *testing.T) {
	for _, c := range []struct {
		status    int
		locations []string
		opts      DocumentOptions
	}{
		{302, []string{"https://other.example/job"}, DocumentOptions{SameOrigin: true}},
		{302, []string{"https://other.example/job"}, DocumentOptions{PublicHeaders: true}},
		{302, []string{"/one", "/two"}, DocumentOptions{SameOrigin: true}},
		{302, nil, DocumentOptions{SameOrigin: true}},
		{304, nil, DocumentOptions{SameOrigin: true}},
	} {
		calls := 0
		_, err := fetchDocument(context.Background(), doerFunc(func(r *http.Request) (*http.Response, error) {
			calls++
			response := reply(r, c.status, "")
			for _, v := range c.locations {
				response.Header.Add("Location", v)
			}
			return response, nil
		}), "https://example.com/job", c.opts, noWait)
		if err == nil || calls != 1 {
			t.Fatalf("calls=%d err=%v", calls, err)
		}
	}
	for _, public := range []bool{false, true} {
		calls := 0
		_, err := fetchDocument(context.Background(), doerFunc(func(r *http.Request) (*http.Response, error) {
			calls++
			response := reply(r, 302, "")
			response.Header.Set("Location", r.URL.Path+"/next")
			return response, nil
		}), "https://example.com/job", DocumentOptions{PublicHeaders: public}, noWait)
		want := 21
		if public {
			want = 6
		}
		if err == nil || calls != want {
			t.Fatalf("calls=%d want=%d err=%v", calls, want, err)
		}
	}
}

type documentRoundTrip func(*http.Request) (*http.Response, error)

func (f documentRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestDocumentCookieHandshakeAndPublicCookieStripping(t *testing.T) {
	for _, public := range []bool{false, true} {
		jar, _ := cookiejar.New(nil)
		calls := 0
		client := &http.Client{Jar: jar, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
		client.Transport = documentRoundTrip(func(r *http.Request) (*http.Response, error) {
			calls++
			if calls == 1 {
				response := reply(r, 302, "")
				response.Header.Set("Set-Cookie", "session=approved; Path=/")
				response.Header.Set("Location", r.URL.String())
				return response, nil
			}
			if (r.Header.Get("Cookie") != "") != !public {
				t.Errorf("public=%v cookie presence changed", public)
			}
			return reply(r, 200, "authenticated"), nil
		})
		result, err := fetchDocument(context.Background(), client, "https://example.com/job", DocumentOptions{SameOrigin: true, PublicHeaders: public}, noWait)
		if err != nil || calls != 2 || string(result.Body) != "authenticated" {
			t.Fatalf("calls=%d result=%+v err=%v", calls, result, err)
		}
	}
	calls := 0
	_, err := fetchDocument(context.Background(), doerFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		response := reply(r, 302, "")
		response.Header.Set("Location", r.URL.String())
		return response, nil
	}), "https://example.com/job", DocumentOptions{SameOrigin: true}, noWait)
	if err == nil || calls != 1 {
		t.Fatal("cookie-stable redirect loop followed")
	}
}

func TestDocumentRawBytesBoundsAndConfig(t *testing.T) {
	raw := string([]byte{0x82, 0xa0, 0xe9})
	result, err := fetchDocument(context.Background(), doerFunc(func(r *http.Request) (*http.Response, error) {
		response := reply(r, 200, raw)
		response.Header.Set("Content-Type", "text/html; charset=iso-8859-1")
		return response, nil
	}), "https://example.com/job", DocumentOptions{}, noWait)
	if err != nil || string(result.Body) != raw || result.Bytes != 3 {
		t.Fatal("raw bytes changed")
	}
	_, err = fetchDocument(context.Background(), doerFunc(func(r *http.Request) (*http.Response, error) {
		return reply(r, 200, strings.Repeat("x", maxResponseBytes+1)), nil
	}), "https://example.com/job", DocumentOptions{}, noWait)
	if err == nil {
		t.Fatal("oversized body admitted")
	}
	for _, opts := range []DocumentOptions{
		{Headers: map[string]string{"Authorization": "secret"}, PublicHeaders: true},
		{Headers: map[string]string{"Accept": "text/html"}},
		{Headers: map[string]string{"User-Agent": "bad\nheader"}, PublicHeaders: true},
		{RetryLimits: map[int]int{200: 1}}, {RetryLimits: map[int]int{429: 6}},
	} {
		calls := 0
		_, err := fetchDocument(context.Background(), doerFunc(func(r *http.Request) (*http.Response, error) { calls++; return reply(r, 200, ""), nil }), "https://example.com/job", opts, noWait)
		if err == nil || calls != 0 {
			t.Fatal("invalid configuration caused traffic")
		}
	}
	for _, endpoint := range []string{"http://127.0.0.1/job", "http://10.0.0.5/job", "http://169.254.169.254/job", "http://[::1]/job"} {
		result, err := FetchDocument(context.Background(), endpoint, DocumentOptions{})
		if err == nil || result.Responses != 0 {
			t.Fatalf("private target admitted: %s", endpoint)
		}
	}
}
