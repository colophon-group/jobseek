package worker

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestPDFConfiguredPublicHeadersDoNotCarryCookiesAcrossRedirects(t *testing.T) {
	body := pdfFixtureBinary(t)
	calls := 0
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Header.Get("Cookie") != "" || r.Header.Get("Authorization") != "" || r.UserAgent() != "public-jobs" || r.Header.Get("Accept") != "application/pdf" {
			t.Error("configured public request inherited private state or lost headers")
		}
		if r.URL.Path == "/job.pdf" {
			http.SetCookie(w, &http.Cookie{Name: "redirect-cookie", Value: "fixture", Path: "/"})
			w.Header().Set("Location", "/final.pdf")
			w.WriteHeader(302)
			return
		}
		if r.Host != "documents.example.com" || r.URL.Path != "/final.pdf" {
			t.Error("public request left source")
		}
		w.Header().Set("Content-Type", "application/pdf")
		w.Write(body)
	}))
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	u, _ := url.Parse("https://documents.example.com/job.pdf")
	jar.SetCookies(u, []*http.Cookie{{Name: "inherited-cookie", Value: "fixture", Path: "/"}})
	client.client.Jar = jar
	p := queue.WorkdayDetailProfile{Profile: "pdf.public-detail/v1", SourceURL: u.String(), Endpoint: u.String(), PDFConfig: map[string]any{"title_source": "text", "request_headers": map[string]any{" User-Agent ": " public-jobs ", "Accept": "application/pdf"}}}
	out, reservation, err := fetchPDFDetail(context.Background(), client.client, p)
	if err != nil || reservation != nil || out["title"] != "Software Engineer in Zurich" || calls != 2 {
		t.Fatal(err, reservation, out, calls)
	}
}

func pdfFixtureBinary(t *testing.T) []byte {
	t.Helper()
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_pdf_binary.json")
	var cases []struct{ Body string }
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 7 {
		t.Fatal(err)
	}
	body, err := base64.StdEncoding.DecodeString(cases[0].Body)
	if err != nil {
		t.Fatal(err)
	}
	return body
}

func pdfFetchFixture(t *testing.T, mode string, body []byte) http.HandlerFunc {
	t.Helper()
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" || r.Host != "documents.example.com" || (r.URL.Path != "/job.pdf" && r.URL.Path != "/final.pdf") {
			t.Error("unbound PDF GET", r.URL)
			w.WriteHeader(400)
			return
		}
		w.Header().Set("Content-Type", "application/pdf")
		w.Header().Set("ETag", `"strong-document-123"`)
		w.Header().Set("Last-Modified", "Thu, 01 Oct 2026 12:00:00 GMT")
		w.Header().Set("Content-Length", fmt.Sprint(len(body)))
		if mode == "reserved" || mode == "503-reserved" {
			w.Header().Set("TDM-Reservation", "1")
			w.Header().Set("TDM-Policy", "https://documents.example.com/policy")
		}
		if (mode == "redirect" || mode == "foreign") && r.URL.Path == "/job.pdf" {
			target := "/final.pdf"
			if mode == "foreign" {
				target = "https://other.example/job.pdf"
			}
			w.Header().Set("Location", target)
			w.Header().Del("Content-Length")
			w.WriteHeader(302)
			return
		}
		switch mode {
		case "404":
			w.Header().Del("Content-Length")
			w.WriteHeader(404)
			return
		case "503", "503-reserved":
			w.Header().Del("Content-Length")
			w.WriteHeader(503)
			return
		case "etag-drift":
			w.Header().Set("ETag", `"strong-document-456"`)
		case "weak":
			w.Header().Set("ETag", `W/"strong-document-123"`)
		case "oversize":
			w.Header().Set("Content-Length", fmt.Sprint(41<<20))
			return
		case "invalid":
			w.Header().Del("Content-Length")
			fmt.Fprint(w, "HTML error")
			return
		}
		w.Write(body)
	}
}

func pdfFingerprintFixtureURL(body []byte) string {
	base := "https://documents.example.com/job.pdf"
	payload := strings.Join([]string{base, `"strong-document-123"`, "2026-10-01T12:00:00+00:00", fmt.Sprint(len(body)), "application/pdf"}, "\n")
	return base + "?_jobseek_fp=" + fmt.Sprintf("%x", sha256.Sum256([]byte(payload)))[:24]
}

func TestPDFHTTPFieldsFingerprintRedirectsPolicyAndBounds(t *testing.T) {
	body := pdfFixtureBinary(t)
	for _, mode := range []string{"complete", "redirect", "reserved", "404", "503", "503-reserved", "etag-drift", "weak", "foreign", "oversize", "invalid"} {
		t.Run(mode, func(t *testing.T) {
			client := verifiedClaimFixtureClient(t, pdfFetchFixture(t, mode, body))
			source := pdfFingerprintFixtureURL(body)
			p := queue.WorkdayDetailProfile{Profile: "pdf.public-detail/v1", SourceURL: source, Endpoint: source, PDFConfig: map[string]any{"title_source": "text"}}
			out, reserved, err := fetchPDFDetail(context.Background(), client.client, p)
			complete := mode == "complete" || mode == "redirect"
			reservation := mode == "reserved" || mode == "503-reserved"
			if complete {
				if err != nil || reserved != nil || out["title"] != "Software Engineer in Zurich" {
					t.Fatal(err, reserved, out)
				}
				return
			}
			if reservation {
				if reserved == nil || reserved.URL != source || out != nil {
					t.Fatal("publisher reservation lost", err, reserved, out)
				}
				return
			}
			if err == nil || out != nil || reserved != nil {
				t.Fatal("failed PDF returned fields", err, reserved, out)
			}
		})
	}
	for _, source := range []string{"https://documents.example.com/job.pdf?_jobseek_fp=bad", "https://documents.example.com/job.pdf?_jobseek_fp=012345678901234567890123&x=1", "https://documents.example.com/job.pdf?%5Fjobseek_fp=012345678901234567890123"} {
		if _, _, err := pdfFingerprintSource(source); err == nil {
			t.Fatal("invalid private fingerprint accepted", source)
		}
	}
}

func TestRealPDFIndependentCanonicalFieldsPolicyFailuresAndEnrichment(t *testing.T) {
	body := pdfFixtureBinary(t)
	for _, mode := range []string{"complete", "enrich", "reserved", "503", "etag-drift"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"scraper_type":"pdf","scraper_config":{"title_source":"text","location_pattern":"(Zurich)"}}`
			if mode == "enrich" {
				metadata = `{"scraper_type":"pdf","scraper_config":{"title_source":"text","location_pattern":"(Zurich)","enrich":["description"]}}`
			}
			f, a, claim := independentDetailOwnedFixture(t, metadata, pdfFingerprintFixtureURL(body))
			ctx := context.Background()
			client := verifiedClaimFixtureClient(t, pdfFetchFixture(t, mode, body))
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal(err, result)
			}
			want := "failed"
			if mode == "complete" || mode == "enrich" {
				want = "succeeded"
			}
			if mode == "reserved" {
				want = "publisher_reserved"
			}
			if result.Cycle.Status != want {
				t.Fatal(result.Cycle.Status, want)
			}
			var title string
			if err := f.pg.QueryRow(ctx, "SELECT titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&title); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				if title != "Software Engineer in Zurich" {
					t.Fatal(title)
				}
			} else if title != "Original" {
				t.Fatal("failed/enrichment detail rewrote title", mode, title)
			}
			if mode == "complete" || mode == "enrich" {
				var count int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions WHERE posting_id=$1::uuid AND html LIKE '%Software Engineer in Zurich%' AND NOT r2_uploaded", f.original).Scan(&count); err != nil || count != 1 {
					t.Fatal("description/R2 intent lost", count, err)
				}
			}
		})
	}
}
