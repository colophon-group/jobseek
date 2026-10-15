package jsonld

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestLinkedDocumentValidatesEveryHopAndPreservesStatusPolicyOrder(t *testing.T) {
	for _, mode := range []string{"success", "foreign", "duplicates", "unsupported", "loop", "limit", "retry", "status-policy", "header-policy", "meta-policy", "cancel"} {
		t.Run(mode, func(t *testing.T) {
			calls := 0
			waits := 0
			client := &http.Client{Transport: documentRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				status := 200
				body := "<p>Complete job</p>"
				h := http.Header{"Content-Type": []string{"text/html"}}
				if r.Header.Get("Accept-Language") != "" {
					t.Fatal("main headers leaked")
				}
				switch mode {
				case "success":
					if calls == 1 {
						status = 302
						h.Set("Location", "https://other.example.org/job")
					}
				case "foreign":
					status = 302
					h.Set("Location", "https://untrusted.example.net/job")
				case "duplicates":
					status = 302
					h["Location"] = []string{"/a", "/b"}
				case "unsupported":
					status = 304
				case "loop":
					status = 302
					h.Set("Location", r.URL.String())
				case "limit":
					status = 302
					h.Set("Location", r.URL.String()+"x")
				case "retry":
					if calls < 3 {
						status = 503
					}
				case "status-policy":
					status = 410
					h.Set("TDM-Reservation", "1")
				case "header-policy":
					h.Set("TDM-Reservation", "1")
				case "meta-policy":
					body = `<meta name="tdm-reservation" content="1">`
				case "cancel":
					status = 503
				}
				return &http.Response{StatusCode: status, Header: h, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})}
			validate := func(raw string) error {
				if strings.Contains(raw, "untrusted") {
					return errors.New("host outside allowlist")
				}
				return nil
			}
			result, err := fetchLinkedDocument(context.Background(), "https://example.org/job", validate, client, func(context.Context, time.Duration) error {
				waits++
				if mode == "cancel" {
					return context.Canceled
				}
				return nil
			})
			switch mode {
			case "success":
				if err != nil || calls != 2 || result.FinalURL != "https://other.example.org/job" {
					t.Fatal(result, err, calls)
				}
			case "retry":
				if err != nil || calls != 3 || waits != 2 {
					t.Fatal(result, err, calls, waits)
				}
			case "status-policy":
				if err == nil || result.ErrorKind != "status" || result.TDMSource != "" {
					t.Fatal(result, err)
				}
			case "header-policy", "meta-policy":
				if err == nil || result.ErrorKind != "tdm" {
					t.Fatal(result, err)
				}
			case "limit":
				if err == nil || calls != 11 {
					t.Fatal(result, err, calls)
				}
			case "cancel":
				if !errors.Is(err, context.Canceled) || calls != 1 {
					t.Fatal(result, err, calls)
				}
			default:
				if err == nil || calls != 1 {
					t.Fatal(result, err, calls)
				}
			}
		})
	}
}
