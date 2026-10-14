package dom

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
)

func TestAlternateReadKeepsCanonicalDefaultsAndLinkedDescriptionOnly(t *testing.T) {
	for _, mode := range []string{"linked", "complete", "short-linked", "policy"} {
		t.Run(mode, func(t *testing.T) {
			config := directConfig(t, `,"fetch_url_transform":{"find":"^https://example.org/canonical$","replace":"https://example.org/read"},"defaults_by_url":{"https://example.org/canonical":{"location":"Zurich"}},"linked_description":{"selector":"a.detail","allowed_host_suffixes":[".example.org"],"min_chars":50,"steps":[{"tag":"article","field":"description","html":true}]}`)
			calls := 0
			client := &http.Client{Transport: directRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				body := `<h1>Original Engineer</h1><h2>Role</h2><p>Read the detailed role.</p><a class="detail" href="/detail">Details</a>`
				h := http.Header{"Content-Type": []string{"text/html"}}
				if calls == 1 {
					if r.URL.String() != "https://example.org/read" {
						t.Fatal("alternate URL was not used")
					}
					if mode == "complete" {
						body = `<h1>Original Engineer</h1><h2>Role</h2><p>` + strings.Repeat("Already complete. ", 10) + `</p>`
					}
				} else {
					if r.URL.String() != "https://example.org/detail" {
						t.Fatal("link used noncanonical base")
					}
					body = `<h1>Do not replace title</h1><article>` + strings.Repeat("Complete description. ", 10) + `</article>`
					if mode == "short-linked" {
						body = "<article>Too short</article>"
					}
					if mode == "policy" {
						h.Set("TDM-Reservation", "1")
					}
				}
				return &http.Response{StatusCode: 200, Header: h, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})}
			result, err := FetchDetailWithClient(context.Background(), "https://example.org/canonical", config, client)
			if mode == "short-linked" || mode == "policy" {
				if err == nil || calls != 2 {
					t.Fatal(result, err, calls)
				}
				if mode == "policy" && (result.ErrorKind != "tdm" || result.FinalURL != "https://example.org/detail") {
					t.Fatal(result)
				}
				return
			}
			expected := 2
			if mode == "complete" {
				expected = 1
			}
			if err != nil || calls != expected || result.Requests != expected || result.Content["title"] != "Original Engineer" {
				t.Fatal(result, err, calls)
			}
			if mode == "linked" && !strings.Contains(result.Content["description"].(string), "Complete description") {
				t.Fatal(result)
			}
			if result.FinalURL != "https://example.org/read" {
				t.Fatal("secondary fetch changed main final URL")
			}
		})
	}
}
