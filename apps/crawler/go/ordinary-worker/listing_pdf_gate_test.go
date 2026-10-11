package worker

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func listingProofCommands(t *testing.T, text string) {
	t.Helper()
	dir := t.TempDir()
	for name, script := range map[string]string{"pdfinfo": "#!/bin/sh\nprintf 'Pages: 1\\n'\n", "pdftotext": "#!/bin/sh\nexec /bin/cat \"${0%/*}/text\"\n"} {
		if e := os.WriteFile(filepath.Join(dir, name), []byte(script), 0700); e != nil {
			t.Fatal(e)
		}
	}
	if e := os.WriteFile(filepath.Join(dir, "text"), []byte(text), 0600); e != nil {
		t.Fatal(e)
	}
	t.Setenv("PATH", dir)
}

func TestListingPDFHTTPRemovalRedirectBoundsAndPolicy(t *testing.T) {
	for _, mode := range []string{"owned", "expired", "foreign", "ambiguous", "removed", "redirect", "cross-origin", "extensionless", "wrong-media", "not-pdf", "large-header", "invalid-length", "policy", "API-policy"} {
		t.Run(mode, func(t *testing.T) {
			text := "Employer\nDeadline: 11 October 2026"
			if mode == "expired" {
				text = "Employer\nDeadline: 10 October 2026"
			}
			if mode == "foreign" {
				text = "Foreign\nDeadline: 11 October 2026"
			}
			if mode == "ambiguous" {
				text = "Employer Foreign\nDeadline: 11 October 2026"
			}
			listingProofCommands(t, text)
			md := dom.Object{"require_unexpired_pdf": map[string]any{"pattern": `Deadline: (.+)`, "date_format": "%d %B %Y"}, "require_pdf_text": map[string]any{"include": "Employer", "exclude": "Foreign"}}
			provider := "dom"
			if mode == "API-policy" {
				provider = "api_sniffer"
				delete(md, "require_pdf_text")
				md["require_pdf_pattern"] = "Employer"
				md["api_url"] = "https://example.com/api"
				md["json_path"] = "jobs"
				md["url_field"] = "url"
			}
			raw, _ := json.Marshal(md)
			config := map[string]string{"crawler_type": provider, "monitor_needs_browser": "0", "board_url": "https://example.com/careers", "metadata": string(raw)}
			endpoint := config["board_url"]
			if provider == "api_sniffer" {
				endpoint = "https://example.com/api"
			}
			p := queue.GreenhouseMonitorProfile{Provider: provider, Profile: "dom.direct-urls/v1", Endpoint: endpoint, BoardID: "fixture", EffectiveConfigSHA256: "bound"}
			proof, e := dom.PDFListingProofOptions(md)
			if e != nil {
				t.Fatal(e)
			}
			source := "https://example.com/posting.pdf"
			if mode == "extensionless" || mode == "wrong-media" {
				source = "https://example.com/document"
			}
			calls := 0
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				status := 200
				h := http.Header{"Content-Type": []string{"application/pdf"}}
				body := "%PDF bounded fixture"
				if mode == "removed" {
					status = 410
				}
				if (mode == "redirect" || mode == "extensionless" || mode == "wrong-media" || mode == "cross-origin") && calls == 1 {
					status = 302
					h.Set("Location", "/final.pdf")
					if mode == "cross-origin" {
						h.Set("Location", "https://foreign.example/final.pdf")
					}
				}
				if mode == "wrong-media" && calls == 2 {
					h.Set("Content-Type", "text/html")
				}
				if mode == "not-pdf" {
					body = "<html>changed</html>"
				}
				if mode == "large-header" {
					h.Set("Content-Length", "30000000")
				}
				if mode == "invalid-length" {
					h.Set("Content-Length", "nonnumeric")
				}
				if mode == "policy" || mode == "API-policy" {
					h.Set("TDM-Reservation", "1")
					h.Set("TDM-Policy", "https://example.com/policy")
					status = 404
				}
				return &http.Response{StatusCode: status, Header: h, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})}
			in := RichDiscovery{Jobs: []RichMonitorJob{{URL: source, URLOnly: true}}}
			now := time.Date(2026, 10, 11, 12, 0, 0, 0, time.UTC)
			found, e := filterListingPDFInventory(context.Background(), client, p, config, in, proof, nil, now)
			failed := mode == "ambiguous" || mode == "cross-origin" || mode == "wrong-media" || mode == "not-pdf" || mode == "large-header" || mode == "invalid-length" || mode == "policy" || mode == "API-policy"
			if (e != nil) != failed {
				t.Fatal("PDF outcome changed", mode, e)
			}
			want := 1
			if failed || mode == "expired" || mode == "foreign" || mode == "removed" {
				want = 0
			}
			if len(found.Jobs) != want {
				t.Fatal("partial/expired inventory delivered", found.Jobs, e)
			}
			if mode == "expired" && found.VerifiedEmptyReason == "" || (mode == "removed" || mode == "foreign") && found.VerifiedEmptyReason != "" {
				t.Fatal("expiry proof fabricated or lost")
			}
			if mode == "policy" || mode == "API-policy" {
				if found.Response == nil || !found.Response.reserved {
					t.Fatal("publisher policy swallowed")
				}
				matches := domVerificationResponseMatches(p, config, found.Response)
				if provider == "api_sniffer" {
					matches = pdfVerificationResponseMatches(p, config, found.Response)
				}
				if !matches {
					t.Fatal("publisher child lost exact parent binding")
				}
				changed := p
				changed.BoardID = "foreign"
				if domVerificationResponseMatches(changed, config, found.Response) || pdfVerificationResponseMatches(changed, config, found.Response) {
					t.Fatal("foreign board reused child")
				}
			}
			if mode == "cross-origin" && calls != 1 {
				t.Fatal("cross-origin hop followed")
			}
		})
	}
}

func TestCurrentCapturedStaticListingControls(t *testing.T) {
	dir := os.Getenv("JOBSEEK_LISTING_BASELINE_DIR")
	if dir == "" {
		t.Skip("explicit protected public replay directory required")
	}
	for _, slug := range []string{"travel-leisure-co-rci-mexico", "canton-of-fribourg-teaching", "international-gymnastics-federation-jobs", "swiss-confederation-united-kingdom", "swiss-confederation-australia", "swiss-confederation-united-states"} {
		t.Run(slug, func(t *testing.T) {
			var c struct {
				Slug, Status string
				Config       struct {
					BoardURL string `json:"board_url"`
					Metadata map[string]any
				}
				URLs     []string
				Captures []struct {
					URL, File, ContentType string
					Status                 int
				}
			}
			raw, e := os.ReadFile(filepath.Join(dir, slug+".json"))
			if e != nil || json.Unmarshal(raw, &c) != nil {
				t.Fatal("captured original missing", e)
			}
			// The capture's content_type spelling is read explicitly below.
			var rawCapture struct{ Captures []map[string]any }
			json.Unmarshal(raw, &rawCapture)
			resources := map[string]int{}
			for i, r := range c.Captures {
				resources[r.URL] = i
			}
			md, _ := json.Marshal(c.Config.Metadata)
			config := map[string]string{"crawler_type": "dom", "monitor_needs_browser": "0", "board_url": c.Config.BoardURL, "metadata": string(md)}
			p := queue.GreenhouseMonitorProfile{Provider: "dom", Profile: "dom.direct-urls/v1", Endpoint: c.Config.BoardURL, BoardID: slug, EffectiveConfigSHA256: "bound"}
			var mu sync.Mutex
			calls := []string{}
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				mu.Lock()
				calls = append(calls, r.URL.String())
				mu.Unlock()
				i, ok := resources[r.URL.String()]
				if !ok {
					t.Error("unrecorded resource", r.URL)
					return nil, queue.ErrConfiguration
				}
				capture := c.Captures[i]
				raw, e := os.ReadFile(filepath.Join(dir, capture.File))
				if e != nil {
					return nil, e
				}
				z, e := gzip.NewReader(bytes.NewReader(raw))
				if e != nil {
					return nil, e
				}
				body, e := io.ReadAll(z)
				z.Close()
				if e != nil {
					return nil, e
				}
				ctype, _ := rawCapture.Captures[i]["content_type"].(string)
				return &http.Response{StatusCode: capture.Status, Header: http.Header{"Content-Type": []string{ctype}}, Body: io.NopCloser(bytes.NewReader(body)), Request: r}, nil
			})}
			found, e := discoverDOMInventory(context.Background(), client, p, config)
			if (e == nil) != (c.Status == "succeeded") {
				t.Fatal("original public outcome differs", c.Status, e)
			}
			if e != nil {
				if len(found.Jobs) > 0 {
					t.Fatal("failed public inventory delivered")
				}
				return
			}
			urls := []string{}
			for _, j := range found.Jobs {
				urls = append(urls, j.URL)
			}
			sort.Strings(urls)
			sort.Strings(c.URLs)
			if !reflect.DeepEqual(urls, c.URLs) {
				t.Fatal("original public inventory differs", urls, c.URLs)
			}
			if slug == "travel-leisure-co-rci-mexico" && len(calls) != 21 {
				t.Fatal("incomplete public numbered scan", len(calls))
			}
		})
	}
}

func TestCurrentCapturedAPIPDFInventory(t *testing.T) {
	dir := os.Getenv("JOBSEEK_API_PDF_BASELINE_DIR")
	if dir == "" {
		t.Skip("explicit protected public API replay directory required")
	}
	var c struct {
		Status    string
		URLs      []string
		Canonical map[string]string `json:"canonical_config"`
		Captures  []struct {
			URL, File   string
			Status      int
			ContentType string `json:"content_type"`
		}
	}
	raw, err := os.ReadFile(filepath.Join(dir, "european-athletics-directus-careers.json"))
	if err != nil || json.Unmarshal(raw, &c) != nil {
		t.Fatal("original API reference missing", err)
	}
	o, err := queue.APISnifferMonitorOptions(c.Canonical)
	if err != nil || o.PDFProof == nil {
		t.Fatal("original PDF controls unbound", err)
	}
	p := queue.GreenhouseMonitorProfile{Provider: "api_sniffer", Profile: "api_sniffer.http-items/v1", Endpoint: o.Endpoint}
	resources := map[string]int{}
	for i, r := range c.Captures {
		resources[r.URL] = i
	}
	var mu sync.Mutex
	calls := 0
	client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		mu.Lock()
		calls++
		mu.Unlock()
		i, ok := resources[r.URL.String()]
		if !ok {
			// Query-key order differs between httpx and Go encoding. Require
			// the same method, origin, path and complete decoded parameters.
			for j, candidate := range c.Captures {
				u, e := url.Parse(candidate.URL)
				if e == nil && r.Method == "GET" && u.Scheme == r.URL.Scheme && u.Host == r.URL.Host && u.EscapedPath() == r.URL.EscapedPath() && reflect.DeepEqual(u.Query(), r.URL.Query()) {
					i, ok = j, true
					break
				}
			}
			if !ok {
				return nil, queue.ErrConfiguration
			}
		}
		capture := c.Captures[i]
		raw, e := os.ReadFile(filepath.Join(dir, capture.File))
		if e != nil {
			return nil, e
		}
		z, e := gzip.NewReader(bytes.NewReader(raw))
		if e != nil {
			return nil, e
		}
		body, e := io.ReadAll(z)
		z.Close()
		if e != nil {
			return nil, e
		}
		return &http.Response{StatusCode: capture.Status, Header: http.Header{"Content-Type": []string{capture.ContentType}}, Body: io.NopCloser(bytes.NewReader(body)), Request: r}, nil
	})}
	found, err := discoverAPISnifferInventory(context.Background(), client, p, c.Canonical)
	if (err == nil) != (c.Status == "succeeded") {
		t.Fatal("current original API/PDF result differs", err, c.Status)
	}
	if err != nil {
		if len(found.Jobs) != 0 {
			t.Fatal("failed inventory delivered")
		}
		return
	}
	urls := []string{}
	for _, j := range found.Jobs {
		urls = append(urls, j.URL)
	}
	sort.Strings(urls)
	sort.Strings(c.URLs)
	if !reflect.DeepEqual(urls, c.URLs) || calls != len(c.Captures) {
		t.Fatal("original API URLs/requests differ", urls, c.URLs, calls, len(c.Captures))
	}
}
