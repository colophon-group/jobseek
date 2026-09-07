package sitemap

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/pilots/go-http-sitemap/boundedhttp"
)

func newHTTPClient(t *testing.T, maxRequests int) *boundedhttp.Client {
	t.Helper()
	client, err := boundedhttp.New(boundedhttp.Config{
		RequestTimeout:           time.Second,
		MaxDecodedBodyBytes:      1 << 20,
		MaxRequests:              maxRequests,
		MaxAggregateDecodedBytes: 4 << 20,
	})
	if err != nil {
		t.Fatal(err)
	}
	return client
}

func newRunner(t *testing.T, client *boundedhttp.Client, config Config) *Runner {
	t.Helper()
	runner, err := New(client, config)
	if err != nil {
		t.Fatal(err)
	}
	return runner
}

func TestURLSetParitySubset(t *testing.T) {
	for _, namespace := range []string{"", ` xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"`} {
		t.Run(fmt.Sprintf("namespace=%q", namespace), func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if got := r.Header.Get("User-Agent"); got != "jobseek-crawler (+https://jseek.co/)" {
					t.Errorf("user-agent=%q", got)
				}
				if got := r.Header.Get("Accept"); got != "application/xml,text/xml,*/*;q=0.8" {
					t.Errorf("accept=%q", got)
				}
				_, _ = fmt.Fprintf(w, `<urlset%s>
<url><loc>https://old.example/jobs/2?utm_source=x&amp;id=2</loc></url>
<url><loc>https://old.example/jobs/skip</loc></url>
<url><loc>https://old.example/about</loc></url>
<url><loc>https://old.example/jobs/2?id=2</loc></url>
</urlset>`, namespace)
			}))
			defer server.Close()

			runner := newRunner(t, newHTTPClient(t, 1), Config{
				SitemapURL:       server.URL,
				IncludeLiteral:   "/jobs/",
				ExcludeLiteral:   "/skip",
				ReplacePrefix:    "https://old.example",
				Replacement:      "https://new.example",
				MaxURLs:          50_000,
				MaxIndexChildren: 10,
			})
			result, err := runner.Run(context.Background())
			if err != nil {
				t.Fatal(err)
			}
			want := []string{"https://new.example/jobs/2?id=2"}
			if !reflect.DeepEqual(result.URLs, want) || result.FilteredCount != 2 || result.NewSitemapURL != "" || result.Truncated {
				t.Fatalf("result=%+v", result)
			}
		})
	}
}

func TestURLSetIgnoresEntriesWithoutUsableLocation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`<urlset><url/><url><loc> </loc></url><unknown/></urlset>`))
	}))
	defer server.Close()

	runner := newRunner(t, newHTTPClient(t, 1), Config{SitemapURL: server.URL, MaxURLs: 50_000, MaxIndexChildren: 1})
	result, err := runner.Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(result.URLs) != 0 || result.TransportMetrics.Requests != 1 {
		t.Fatalf("result=%+v", result)
	}
}

func TestOneLevelIndexAndMissingChild(t *testing.T) {
	var nonJobRequests atomic.Int32
	server := httptest.NewServer(nil)
	defer server.Close()
	mux := http.NewServeMux()
	mux.HandleFunc("/index.xml", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = fmt.Fprintf(w, `<sitemapindex xmlns="https://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>%s/jobs-1.xml</loc></sitemap>
<sitemap><loc>%s/jobs-missing.xml</loc></sitemap>
<sitemap><loc>%s/jobs-gone.xml</loc></sitemap>
<sitemap><loc>%s/pages.xml</loc></sitemap>
</sitemapindex>`, server.URL, server.URL, server.URL, server.URL)
	})
	mux.HandleFunc("/jobs-1.xml", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`<urlset><url><loc>https://example.test/jobs/1</loc></url></urlset>`))
	})
	mux.HandleFunc("/jobs-missing.xml", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNotFound) })
	mux.HandleFunc("/jobs-gone.xml", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusGone) })
	mux.HandleFunc("/pages.xml", func(w http.ResponseWriter, _ *http.Request) { nonJobRequests.Add(1) })
	server.Config.Handler = mux

	runner := newRunner(t, newHTTPClient(t, 4), Config{SitemapURL: server.URL + "/index.xml", MaxURLs: 50_000, MaxIndexChildren: 10})
	result, err := runner.Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"https://example.test/jobs/1"}; !reflect.DeepEqual(result.URLs, want) {
		t.Fatalf("urls=%v", result.URLs)
	}
	if result.TransportMetrics.Requests != 4 || nonJobRequests.Load() != 0 {
		t.Fatalf("metrics=%+v non_job_requests=%d", result.TransportMetrics, nonJobRequests.Load())
	}
}

func TestIndexDeduplicatesChildrenBeforeLimitsAndFetch(t *testing.T) {
	var childRequests atomic.Int32
	server := httptest.NewServer(nil)
	defer server.Close()
	mux := http.NewServeMux()
	mux.HandleFunc("/index.xml", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = fmt.Fprintf(w, `<sitemapindex><sitemap><loc>%s/jobs.xml</loc></sitemap><sitemap><loc>%s/jobs.xml</loc></sitemap></sitemapindex>`, server.URL, server.URL)
	})
	mux.HandleFunc("/jobs.xml", func(w http.ResponseWriter, _ *http.Request) {
		childRequests.Add(1)
		_, _ = w.Write([]byte(`<urlset><url><loc>https://example.test/jobs/1</loc></url></urlset>`))
	})
	server.Config.Handler = mux

	runner := newRunner(t, newHTTPClient(t, 2), Config{SitemapURL: server.URL + "/index.xml", MaxURLs: 50_000, MaxIndexChildren: 1})
	result, err := runner.Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if childRequests.Load() != 1 || result.TransportMetrics.Requests != 2 {
		t.Fatalf("child_requests=%d metrics=%+v", childRequests.Load(), result.TransportMetrics)
	}
}

func TestChildFailureIsAtomic(t *testing.T) {
	for _, status := range []int{
		http.StatusBadRequest,
		http.StatusUnauthorized,
		http.StatusForbidden,
		http.StatusRequestTimeout,
		http.StatusAccepted,
		http.StatusTooEarly,
		http.StatusTooManyRequests,
		http.StatusInternalServerError,
		599,
	} {
		t.Run(fmt.Sprintf("status=%d", status), func(t *testing.T) {
			server := httptest.NewServer(nil)
			defer server.Close()
			mux := http.NewServeMux()
			mux.HandleFunc("/index.xml", func(w http.ResponseWriter, _ *http.Request) {
				_, _ = fmt.Fprintf(w, `<sitemapindex><sitemap><loc>%s/jobs-good.xml</loc></sitemap><sitemap><loc>%s/jobs-bad.xml</loc></sitemap></sitemapindex>`, server.URL, server.URL)
			})
			mux.HandleFunc("/jobs-good.xml", func(w http.ResponseWriter, _ *http.Request) {
				_, _ = w.Write([]byte(`<urlset><url><loc>https://example.test/jobs/1</loc></url></urlset>`))
			})
			mux.HandleFunc("/jobs-bad.xml", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(status) })
			server.Config.Handler = mux

			runner := newRunner(t, newHTTPClient(t, 3), Config{SitemapURL: server.URL + "/index.xml", MaxURLs: 50_000, MaxIndexChildren: 10})
			result, err := runner.Run(context.Background())
			if err == nil || len(result.URLs) != 0 {
				t.Fatalf("result=%+v err=%v", result, err)
			}
			var sitemapErr *Error
			if !errors.As(err, &sitemapErr) || sitemapErr.Kind != ErrorStatus || sitemapErr.Status != status {
				t.Fatalf("error=%#v", err)
			}
			if result.TransportMetrics.Requests != 3 || result.TransportMetrics.DecodedBytes == 0 {
				t.Fatalf("metrics=%+v", result.TransportMetrics)
			}
		})
	}
}

func TestRequestCapAndUnsupportedNestedIndexAreAtomic(t *testing.T) {
	server := httptest.NewServer(nil)
	defer server.Close()
	mux := http.NewServeMux()
	mux.HandleFunc("/index.xml", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = fmt.Fprintf(w, `<sitemapindex><sitemap><loc>%s/jobs-1.xml</loc></sitemap><sitemap><loc>%s/jobs-2.xml</loc></sitemap></sitemapindex>`, server.URL, server.URL)
	})
	mux.HandleFunc("/jobs-1.xml", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`<urlset><url><loc>https://example.test/jobs/1</loc></url></urlset>`))
	})
	mux.HandleFunc("/jobs-2.xml", func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte(`<sitemapindex/>`)) })
	server.Config.Handler = mux

	for _, maxRequests := range []int{2, 3} {
		runner := newRunner(t, newHTTPClient(t, maxRequests), Config{SitemapURL: server.URL + "/index.xml", MaxURLs: 50_000, MaxIndexChildren: 10})
		result, err := runner.Run(context.Background())
		if err == nil || len(result.URLs) != 0 {
			t.Fatalf("max_requests=%d result=%+v err=%v", maxRequests, result, err)
		}
		if result.TransportMetrics.Requests != maxRequests || result.TransportMetrics.DecodedBytes == 0 {
			t.Fatalf("max_requests=%d metrics=%+v", maxRequests, result.TransportMetrics)
		}
	}
}

func TestLargeChildErrorBodyDoesNotMaskStatus(t *testing.T) {
	for _, status := range []int{http.StatusNotFound, http.StatusGone, http.StatusInternalServerError} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			server := httptest.NewServer(nil)
			defer server.Close()
			mux := http.NewServeMux()
			mux.HandleFunc("/index.xml", func(w http.ResponseWriter, _ *http.Request) {
				_, _ = fmt.Fprintf(w, `<sitemapindex><sitemap><loc>%s/jobs.xml</loc></sitemap></sitemapindex>`, server.URL)
			})
			mux.HandleFunc("/jobs.xml", func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(status)
				_, _ = w.Write([]byte(strings.Repeat("x", 2<<20)))
			})
			server.Config.Handler = mux

			runner := newRunner(t, newHTTPClient(t, 2), Config{SitemapURL: server.URL + "/index.xml", MaxURLs: 50_000, MaxIndexChildren: 1})
			result, err := runner.Run(context.Background())
			if status == http.StatusNotFound || status == http.StatusGone {
				if err == nil {
					t.Fatal("expected no-usable-child error")
				}
			} else {
				var sitemapErr *Error
				if !errors.As(err, &sitemapErr) || sitemapErr.Kind != ErrorStatus || sitemapErr.Status != status {
					t.Fatalf("error=%v", err)
				}
			}
			if len(result.URLs) != 0 || result.TransportMetrics.Requests != 2 {
				t.Fatalf("result=%+v", result)
			}
		})
	}
}

func TestStripUTMPreservesPythonKeyOrderAndMalformedQueries(t *testing.T) {
	if got, want := stripUTM("https://example.test/jobs/1?b=2&utm_source=x&a=1"), "https://example.test/jobs/1?b=2&a=1"; got != want {
		t.Fatalf("got=%q want=%q", got, want)
	}
	for _, rawURL := range []string{
		"https://example.test/jobs/1?a=1;bad=2&utm_source=x",
		"https://example.test/jobs/1?a=%zz&utm_source=x",
	} {
		if got := stripUTM(rawURL); got != rawURL {
			t.Fatalf("malformed query changed: got=%q want=%q", got, rawURL)
		}
	}
}

func TestErrorsRedactSitemapURLSecrets(t *testing.T) {
	client := newHTTPClient(t, 1)
	_, err := New(client, Config{SitemapURL: "https://user:password@example.test/sitemap.xml?token=secret#fragment", MaxURLs: 1, MaxIndexChildren: 1})
	var sitemapErr *Error
	if !errors.As(err, &sitemapErr) || sitemapErr.URL != "https://example.test/sitemap.xml" || strings.Contains(fmt.Sprintf("%+v", sitemapErr), "secret") || strings.Contains(fmt.Sprintf("%+v", sitemapErr), "password") {
		t.Fatalf("error=%+v", sitemapErr)
	}
}

func TestTruncatesBeforeDuplicateRemovalAndFiltering(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`<urlset><url><loc>https://example.test/jobs/a</loc></url><url><loc>https://example.test/jobs/a</loc></url><url><loc>https://example.test/jobs/b</loc></url></urlset>`))
	}))
	defer server.Close()

	runner := newRunner(t, newHTTPClient(t, 1), Config{SitemapURL: server.URL, IncludeLiteral: "/jobs/", MaxURLs: 2, MaxIndexChildren: 1})
	result, err := runner.Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if !result.Truncated || !reflect.DeepEqual(result.URLs, []string{"https://example.test/jobs/a"}) {
		t.Fatalf("result=%+v", result)
	}
}

func TestInvalidConfigMakesNoRequest(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { requests.Add(1) }))
	defer server.Close()
	client := newHTTPClient(t, 1)
	if _, err := New(client, Config{SitemapURL: server.URL, MaxURLs: 50_001, MaxIndexChildren: 1}); err == nil {
		t.Fatal("expected validation error")
	}
	if requests.Load() != 0 {
		t.Fatalf("requests=%d", requests.Load())
	}
}
