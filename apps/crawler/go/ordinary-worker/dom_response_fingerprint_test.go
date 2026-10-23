package worker

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func fingerprintHeaders() http.Header {
	h := http.Header{}
	h.Set("Content-Type", "application/pdf; version=1.7")
	h.Set("Content-Length", "1024")
	h.Set("Last-Modified", "Thu, 22 Oct 2026 10:30:00 GMT")
	h.Set("ETag", `"strong-opaque-validator-001"`)
	return h
}

func TestResponseFingerprintMatchesOriginalPythonValidatorsAndIdentity(t *testing.T) {
	body, err := os.ReadFile("testdata/python_response_fingerprints.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Source string
		Headers      map[string]string
		Expected     string `json:"expected_url"`
		Error        bool
	}
	if json.Unmarshal(body, &cases) != nil || len(cases) != 12 {
		t.Fatal("original fingerprint oracle unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			r := &http.Response{Header: http.Header{}}
			for k, v := range c.Headers {
				r.Header.Set(k, v)
			}
			base, _, err := pdfFingerprintSource(c.Source)
			if err != nil {
				t.Fatal(err)
			}
			token, err := responseFingerprintToken(base, "application/pdf", r, nil)
			if (err != nil) != c.Error {
				t.Fatal("original validator disposition differs", err)
			}
			if err != nil {
				return
			}
			separator := "?"
			if strings.Contains(base, "?") {
				separator = "&"
			}
			actual := base + separator + "_jobseek_fp=" + token
			if actual != c.Expected {
				t.Fatal("mutable identity differs", actual, c.Expected)
			}
			if err := pdfVerifyFingerprint(base, token, r, make([]byte, 1024)); err != nil {
				t.Fatal("HEAD token not verified independently by detail GET", err)
			}
			if err := pdfVerifyFingerprint(base, token, r, make([]byte, 1023)); err == nil {
				t.Fatal("GET body length drift accepted")
			}
		})
	}
}

type fingerprintHeadBody struct{ closed *atomic.Int64 }

func (b fingerprintHeadBody) Read([]byte) (int, error) {
	return 0, errors.New("HEAD must not read a document body")
}
func (b fingerprintHeadBody) Close() error { b.closed.Add(1); return nil }

func TestDOMMutableDocumentInventoryPublishesExactValidatedURLs(t *testing.T) {
	board := "https://careers.example.com/listing"
	config := map[string]string{"crawler_type": "dom", "board_url": board, "metadata": `{"link_selector":"a[href]","fingerprint_response":{"content_type":"application/pdf"},"scraper_type":"pdf"}`}
	p := queue.GreenhouseMonitorProfile{BoardID: "board", Provider: "dom", Profile: "dom.direct-urls/v1", Endpoint: board, EffectiveConfigSHA256: "bound"}
	var heads, closed atomic.Int64
	client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		if r.Method == "GET" {
			return &http.Response{StatusCode: 200, Request: r, Header: http.Header{"Content-Type": []string{"text/html"}}, Body: io.NopCloser(strings.NewReader(`<a href="/vacancy.pdf?lang=en&amp;blank=&amp;encoded=%2F#section">Role</a>`))}, nil
		}
		if r.Method != "HEAD" {
			t.Error("unexpected document method", r.Method)
		}
		heads.Add(1)
		return &http.Response{StatusCode: 200, Request: r, Header: fingerprintHeaders(), Body: fingerprintHeadBody{&closed}}, nil
	})}
	found, err := discoverDOMInventory(context.Background(), client, p, config)
	if err != nil || len(found.Jobs) != 1 || heads.Load() != 1 || closed.Load() != 1 {
		t.Fatal("complete HEAD inventory or cleanup differs", len(found.Jobs), err, heads.Load(), closed.Load())
	}
	base := "https://careers.example.com/vacancy.pdf?lang=en&blank=&encoded=%2F"
	token, _ := responseFingerprintToken(base, "application/pdf", &http.Response{Header: fingerprintHeaders()}, nil)
	if found.Jobs[0].URL != base+"&_jobseek_fp="+token {
		t.Fatal("discovered fingerprint identity differs", found.Jobs[0].URL)
	}
}

func TestDOMFingerprintRedirectPolicyFailureAndParentBinding(t *testing.T) {
	for _, mode := range []string{"same-origin", "cross-origin", "303", "missing-validator", "reserved-error", "loop", "header-origin"} {
		t.Run(mode, func(t *testing.T) {
			board := "https://careers.example.com/listing"
			source := "https://careers.example.com/role.pdf"
			config := map[string]string{"crawler_type": "dom", "board_url": board, "metadata": `{"fingerprint_response":{"content_type":"application/pdf"},"scraper_type":"pdf"}`}
			p := queue.GreenhouseMonitorProfile{BoardID: "board", Provider: "dom", Profile: "dom.direct-urls/v1", Endpoint: board, EffectiveConfigSHA256: "bound"}
			c, err := queue.DOMMonitorOptions(config)
			if err != nil {
				t.Fatal(err)
			}
			if mode == "header-origin" {
				c.Document.Headers = map[string]string{"X-Public": "value"}
				source = "https://assets.example.com/role.pdf"
			}
			calls := 0
			var closed atomic.Int64
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				headers := fingerprintHeaders()
				status := 200
				switch mode {
				case "same-origin":
					if calls == 1 {
						status = 302
						headers.Set("Location", "/fresh.pdf#anchor")
					}
				case "cross-origin":
					status = 302
					headers.Set("Location", "https://elsewhere.example.com/role.pdf")
				case "303":
					status = 303
					headers.Set("Location", "/fresh.pdf")
				case "missing-validator":
					headers.Del("ETag")
				case "reserved-error":
					status = 503
					headers.Set("TDM-Reservation", "1")
				case "loop":
					status = 302
					headers.Set("Location", "/role.pdf")
				}
				return &http.Response{StatusCode: status, Request: r, Header: headers, Body: fingerprintHeadBody{&closed}}, nil
			})}
			url, response, err := fingerprintDOMURL(context.Background(), client, p, config, source, c)
			if mode == "same-origin" {
				if err != nil || url == "" || calls != 2 {
					t.Fatal(url, err, calls)
				}
			} else if err == nil || url != "" {
				t.Fatal("unproved identity accepted", mode, url, err)
			}
			if calls != int(closed.Load()) {
				t.Fatal("HEAD response not closed")
			}
			if mode == "header-origin" && calls != 0 {
				t.Fatal("configured headers reached foreign origin")
			}
			if mode == "cross-origin" && calls != 1 {
				t.Fatal("followed foreign redirect")
			}
			if mode == "reserved-error" {
				if response == nil || !response.reserved || !domVerificationResponseMatches(p, config, response) {
					t.Fatal("reservation lost its sealed parent binding", response)
				}
				config["metadata"] = `{"fingerprint_response":{"content_type":"application/pdf"},"scraper_type":"pdf","link_selector":"a[href]"}`
				if domVerificationResponseMatches(p, config, response) {
					t.Fatal("reservation survived changed original configuration")
				}
			}
		})
	}
}

func TestDOMFingerprintFailureDiscardsPrefixAndDrainsCanceledSiblings(t *testing.T) {
	p := queue.GreenhouseMonitorProfile{Provider: "dom", Endpoint: "https://careers.example.com/listing"}
	c := dom.ListingConfig{ResponseFingerprintType: "application/pdf"}
	input := RichDiscovery{Jobs: []RichMonitorJob{{URL: "https://careers.example.com/a.pdf"}, {URL: "https://careers.example.com/b.pdf"}, {URL: "https://careers.example.com/c.pdf"}}}
	var active atomic.Int64
	client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		active.Add(1)
		defer active.Add(-1)
		if r.URL.Path == "/a.pdf" {
			return &http.Response{StatusCode: 503, Request: r, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(""))}, nil
		}
		<-r.Context().Done()
		return nil, r.Context().Err()
	})}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	found, err := fingerprintDOMInventory(ctx, client, p, map[string]string{}, input, c)
	if err == nil || len(found.Jobs) != 0 || active.Load() != 0 {
		t.Fatal("failed inventory retained a prefix or request", err, len(found.Jobs), active.Load())
	}
	input.Jobs = make([]RichMonitorJob, 101)
	if _, err := fingerprintDOMInventory(context.Background(), client, p, nil, input, c); err == nil {
		t.Fatal("document verification budget ignored")
	}
}
