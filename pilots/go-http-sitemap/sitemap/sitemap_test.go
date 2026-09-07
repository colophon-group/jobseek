package sitemap

import (
	"context"
	"errors"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
)

type triggeredContext struct {
	context.Context
	done chan struct{}
	err  error
}

type getStep struct {
	response boundedhttp.Response
	err      error
}

type sequenceGetter struct {
	steps []getStep
	calls int
}

func (g *sequenceGetter) Get(context.Context, string, http.Header) (boundedhttp.Response, error) {
	step := g.steps[g.calls]
	g.calls++
	return step.response, step.err
}

func (c *triggeredContext) Done() <-chan struct{} { return c.done }

func (c *triggeredContext) Err() error {
	select {
	case <-c.done:
		return c.err
	default:
		return nil
	}
}

func newHTTPClient(t *testing.T, maxRequests int) *boundedhttp.Client {
	t.Helper()
	return newHTTPClientWithLimits(t, maxRequests, time.Second, 1<<20, 4<<20)
}

func newHTTPClientWithLimits(t *testing.T, maxRequests int, timeout time.Duration, maxBody, maxAggregate int64) *boundedhttp.Client {
	t.Helper()
	client, err := boundedhttp.New(boundedhttp.Config{
		RequestTimeout:           timeout,
		MaxDecodedBodyBytes:      maxBody,
		MaxRequests:              maxRequests,
		MaxAggregateDecodedBytes: maxAggregate,
	})
	if err != nil {
		t.Fatal(err)
	}
	return client
}

func noWait(runner *Runner, calls *[]time.Duration) {
	runner.sleep = func(_ context.Context, delay time.Duration) error {
		*calls = append(*calls, delay)
		return nil
	}
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
	if _, err := New(client, Config{SitemapURL: server.URL, MaxURLs: 1, MaxIndexChildren: 1, RootMaxAttempts: 4}); err == nil {
		t.Fatal("expected root-attempt validation error")
	}
	if _, err := New(client, Config{SitemapURL: server.URL, MaxURLs: 1, MaxIndexChildren: 1, RootBackoff: time.Duration(math.MaxInt64/2 + 1)}); err == nil {
		t.Fatal("expected root-backoff validation error")
	}
	if requests.Load() != 0 {
		t.Fatalf("requests=%d", requests.Load())
	}
}

func TestRootRetryRecoversRetryableStatuses(t *testing.T) {
	for _, status := range []int{http.StatusAccepted, http.StatusUnauthorized, http.StatusForbidden, http.StatusRequestTimeout, http.StatusTooEarly, http.StatusTooManyRequests, http.StatusInternalServerError, 599} {
		t.Run(fmt.Sprintf("status=%d", status), func(t *testing.T) {
			var requests atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				if requests.Add(1) == 1 {
					w.WriteHeader(status)
					return
				}
				_, _ = w.Write([]byte(`<urlset><url><loc>https://example.test/jobs/1</loc></url></urlset>`))
			}))
			defer server.Close()

			runner := newRunner(t, newHTTPClient(t, 2), Config{SitemapURL: server.URL, MaxURLs: 50_000, MaxIndexChildren: 1, RootBackoff: time.Millisecond})
			var sleeps []time.Duration
			noWait(runner, &sleeps)
			result, err := runner.Run(context.Background())
			if err != nil {
				t.Fatal(err)
			}
			if requests.Load() != 2 || result.TransportMetrics.Requests != 2 || len(sleeps) != 1 || sleeps[0] != time.Millisecond || !reflect.DeepEqual(result.URLs, []string{"https://example.test/jobs/1"}) {
				t.Fatalf("requests=%d sleeps=%v result=%+v", requests.Load(), sleeps, result)
			}
		})
	}
}

func TestDefaultBackoffHonorsCanceledContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := sleepContext(ctx, time.Hour); !errors.Is(err, context.Canceled) {
		t.Fatalf("error=%v", err)
	}
}

func TestRootRetryRecoversEmpty200(t *testing.T) {
	var requests atomic.Int32
	validXML := []byte(`<urlset><url><loc>https://example.test/jobs/1</loc></url></urlset>`)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if requests.Add(1) == 1 {
			w.WriteHeader(http.StatusOK)
			return
		}
		_, _ = w.Write(validXML)
	}))
	defer server.Close()

	runner := newRunner(t, newHTTPClient(t, 2), Config{SitemapURL: server.URL, MaxURLs: 50_000, MaxIndexChildren: 1, RootMaxAttempts: 2, RootBackoff: time.Millisecond})
	var sleeps []time.Duration
	noWait(runner, &sleeps)
	result, err := runner.Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if requests.Load() != 2 || result.TransportMetrics.Requests != 2 || result.TransportMetrics.DecodedBytes != int64(len(validXML)) || !reflect.DeepEqual(sleeps, []time.Duration{time.Millisecond}) || !reflect.DeepEqual(result.URLs, []string{"https://example.test/jobs/1"}) {
		t.Fatalf("requests=%d sleeps=%v result=%+v", requests.Load(), sleeps, result)
	}
}

func TestRootEmpty200ExhaustionIsTypedAndHasNoFinalSleep(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	runner := newRunner(t, newHTTPClient(t, 3), Config{SitemapURL: server.URL + "/sitemap.xml?token=secret", MaxURLs: 50_000, MaxIndexChildren: 1, RootMaxAttempts: 3, RootBackoff: time.Millisecond})
	var sleeps []time.Duration
	noWait(runner, &sleeps)
	result, err := runner.Run(context.Background())
	var exhausted *RetryExhaustedError
	if !errors.As(err, &exhausted) || exhausted.Attempts != 3 || exhausted.LastStatus != http.StatusOK || exhausted.LastOutcome != "empty_body" || exhausted.LastTransportKind != "" || exhausted.URL != server.URL+"/sitemap.xml" || strings.Contains(fmt.Sprintf("%+v", exhausted), "secret") {
		t.Fatalf("retry error=%+v err=%v", exhausted, err)
	}
	if requests.Load() != 3 || result.TransportMetrics.Requests != 3 || result.TransportMetrics.DecodedBytes != 0 || len(result.URLs) != 0 || !reflect.DeepEqual(sleeps, []time.Duration{time.Millisecond, 2 * time.Millisecond}) {
		t.Fatalf("requests=%d sleeps=%v result=%+v", requests.Load(), sleeps, result)
	}
}

func TestRootDoesNotRetryNonemptyMalformedXML(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		_, _ = w.Write([]byte(`<urlset>`))
	}))
	defer server.Close()

	runner := newRunner(t, newHTTPClient(t, 3), Config{SitemapURL: server.URL, MaxURLs: 50_000, MaxIndexChildren: 1, RootMaxAttempts: 3, RootBackoff: time.Millisecond})
	var sleeps []time.Duration
	noWait(runner, &sleeps)
	result, err := runner.Run(context.Background())
	var sitemapErr *Error
	if !errors.As(err, &sitemapErr) || sitemapErr.Kind != ErrorXML {
		t.Fatalf("error=%v", err)
	}
	if requests.Load() != 1 || result.TransportMetrics.Requests != 1 || len(sleeps) != 0 || len(result.URLs) != 0 {
		t.Fatalf("requests=%d sleeps=%v result=%+v", requests.Load(), sleeps, result)
	}
}

func TestEmptyChildIsNotRetried(t *testing.T) {
	var childRequests atomic.Int32
	server := httptest.NewServer(nil)
	defer server.Close()
	mux := http.NewServeMux()
	mux.HandleFunc("/index.xml", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = fmt.Fprintf(w, `<sitemapindex><sitemap><loc>%s/jobs.xml</loc></sitemap></sitemapindex>`, server.URL)
	})
	mux.HandleFunc("/jobs.xml", func(w http.ResponseWriter, _ *http.Request) {
		childRequests.Add(1)
		w.WriteHeader(http.StatusOK)
	})
	server.Config.Handler = mux

	runner := newRunner(t, newHTTPClient(t, 3), Config{SitemapURL: server.URL + "/index.xml", MaxURLs: 50_000, MaxIndexChildren: 1, RootMaxAttempts: 3, RootBackoff: time.Millisecond})
	var sleeps []time.Duration
	noWait(runner, &sleeps)
	result, err := runner.Run(context.Background())
	var sitemapErr *Error
	if !errors.As(err, &sitemapErr) || sitemapErr.Kind != ErrorEmptyBody {
		t.Fatalf("error=%v", err)
	}
	if childRequests.Load() != 1 || result.TransportMetrics.Requests != 2 || len(sleeps) != 0 || len(result.URLs) != 0 {
		t.Fatalf("child_requests=%d sleeps=%v result=%+v", childRequests.Load(), sleeps, result)
	}
}

func TestRootRetryExhaustionIsTypedAndDoesNotSleepAfterFinalAttempt(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()

	runner := newRunner(t, newHTTPClient(t, 3), Config{SitemapURL: server.URL + "/sitemap.xml?token=secret#fragment", MaxURLs: 50_000, MaxIndexChildren: 1, RootMaxAttempts: 3, RootBackoff: time.Millisecond})
	var sleeps []time.Duration
	noWait(runner, &sleeps)
	result, err := runner.Run(context.Background())
	var exhausted *RetryExhaustedError
	if !errors.As(err, &exhausted) {
		t.Fatalf("error=%v", err)
	}
	if exhausted.URL != server.URL+"/sitemap.xml" || exhausted.Attempts != 3 || exhausted.LastStatus != http.StatusInternalServerError || exhausted.LastTransportKind != "" || strings.Contains(fmt.Sprintf("%+v", exhausted), "secret") {
		t.Fatalf("retry error=%+v", exhausted)
	}
	if len(result.URLs) != 0 || result.TransportMetrics.Requests != 3 || requests.Load() != 3 || !reflect.DeepEqual(sleeps, []time.Duration{time.Millisecond, 2 * time.Millisecond}) {
		t.Fatalf("requests=%d sleeps=%v result=%+v", requests.Load(), sleeps, result)
	}
}

func TestRootRetryRecoversTimeout(t *testing.T) {
	rootURL := "https://example.test/sitemap.xml"
	validXML := []byte(`<urlset><url><loc>https://example.test/jobs/1</loc></url></urlset>`)
	getter := &sequenceGetter{steps: []getStep{
		{err: &boundedhttp.Error{Kind: boundedhttp.ErrorTimeout, URL: rootURL, Err: context.DeadlineExceeded}},
		{response: boundedhttp.Response{StatusCode: http.StatusOK, Body: validXML}},
	}}
	runner := &Runner{config: Config{SitemapURL: rootURL, RootMaxAttempts: 2, RootBackoff: time.Millisecond}}
	var sleeps []time.Duration
	noWait(runner, &sleeps)
	doc, err := runner.fetchRoot(context.Background(), getter)
	if err != nil || getter.calls != 2 || !reflect.DeepEqual(sleeps, []time.Duration{time.Millisecond}) || !reflect.DeepEqual(extractURLs(doc), []string{"https://example.test/jobs/1"}) {
		t.Fatalf("calls=%d sleeps=%v doc=%+v err=%v", getter.calls, sleeps, doc, err)
	}
}

func TestCallerContextWinsDuringFinalRootAttempt(t *testing.T) {
	for _, test := range []struct {
		name     string
		cause    error
		wantKind ErrorKind
	}{
		{name: "deadline", cause: context.DeadlineExceeded, wantKind: ErrorDeadline},
		{name: "canceled", cause: context.Canceled, wantKind: ErrorCanceled},
	} {
		t.Run(test.name, func(t *testing.T) {
			var requests atomic.Int32
			finalAttemptStarted := make(chan struct{})
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if requests.Add(1) == 1 {
					w.WriteHeader(http.StatusInternalServerError)
					return
				}
				close(finalAttemptStarted)
				<-r.Context().Done()
			}))
			defer server.Close()

			runner := newRunner(t, newHTTPClient(t, 2), Config{SitemapURL: server.URL, MaxURLs: 50_000, MaxIndexChildren: 1, RootMaxAttempts: 2, RootBackoff: time.Millisecond})
			var sleeps []time.Duration
			noWait(runner, &sleeps)
			ctx := &triggeredContext{Context: context.Background(), done: make(chan struct{}), err: test.cause}
			done := make(chan struct {
				result Result
				err    error
			}, 1)
			go func() {
				result, err := runner.Run(ctx)
				done <- struct {
					result Result
					err    error
				}{result, err}
			}()
			<-finalAttemptStarted
			close(ctx.done)
			got := <-done
			var sitemapErr *Error
			var exhausted *RetryExhaustedError
			if !errors.As(got.err, &sitemapErr) || sitemapErr.Kind != test.wantKind || !errors.Is(got.err, test.cause) || errors.As(got.err, &exhausted) {
				t.Fatalf("error=%v", got.err)
			}
			if requests.Load() != 2 || got.result.TransportMetrics.Requests != 2 || len(got.result.URLs) != 0 || !reflect.DeepEqual(sleeps, []time.Duration{time.Millisecond}) {
				t.Fatalf("requests=%d sleeps=%v result=%+v", requests.Load(), sleeps, got.result)
			}
		})
	}
}

func TestRootRetryExhaustsTransportFailures(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	rootURL := server.URL + "/sitemap.xml?token=secret"
	server.Close()

	runner := newRunner(t, newHTTPClient(t, 2), Config{SitemapURL: rootURL, MaxURLs: 50_000, MaxIndexChildren: 1, RootMaxAttempts: 2, RootBackoff: time.Millisecond})
	var sleeps []time.Duration
	noWait(runner, &sleeps)
	result, err := runner.Run(context.Background())
	var exhausted *RetryExhaustedError
	if !errors.As(err, &exhausted) || exhausted.Attempts != 2 || exhausted.LastTransportKind != boundedhttp.ErrorTransport || exhausted.URL != server.URL+"/sitemap.xml" || strings.Contains(fmt.Sprintf("%+v", exhausted), "secret") {
		t.Fatalf("retry error=%+v err=%v", exhausted, err)
	}
	if result.TransportMetrics.Requests != 2 || len(result.URLs) != 0 || len(sleeps) != 1 {
		t.Fatalf("sleeps=%v result=%+v", sleeps, result)
	}
}

func TestRootRetryStopsAtRequestCap(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()

	runner := newRunner(t, newHTTPClient(t, 1), Config{SitemapURL: server.URL, MaxURLs: 50_000, MaxIndexChildren: 1, RootMaxAttempts: 2, RootBackoff: time.Millisecond})
	var sleeps []time.Duration
	noWait(runner, &sleeps)
	result, err := runner.Run(context.Background())
	var boundedErr *boundedhttp.Error
	if !errors.As(err, &boundedErr) || boundedErr.Kind != boundedhttp.ErrorRequestLimit {
		t.Fatalf("error=%v", err)
	}
	if requests.Load() != 1 || result.TransportMetrics.Requests != 1 || len(result.URLs) != 0 || len(sleeps) != 1 {
		t.Fatalf("requests=%d sleeps=%v result=%+v", requests.Load(), sleeps, result)
	}
}

func TestRootRetryCancellationInterruptsBackoff(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()

	runner := newRunner(t, newHTTPClient(t, 2), Config{SitemapURL: server.URL, MaxURLs: 50_000, MaxIndexChildren: 1, RootMaxAttempts: 2, RootBackoff: time.Hour})
	entered := make(chan struct{})
	runner.sleep = func(ctx context.Context, _ time.Duration) error {
		close(entered)
		<-ctx.Done()
		return ctx.Err()
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct {
		result Result
		err    error
	}, 1)
	go func() {
		result, err := runner.Run(ctx)
		done <- struct {
			result Result
			err    error
		}{result, err}
	}()
	<-entered
	cancel()
	got := <-done
	var sitemapErr *Error
	if !errors.As(got.err, &sitemapErr) || sitemapErr.Kind != ErrorCanceled || !errors.Is(got.err, context.Canceled) {
		t.Fatalf("error=%v", got.err)
	}
	if requests.Load() != 1 || got.result.TransportMetrics.Requests != 1 || len(got.result.URLs) != 0 {
		t.Fatalf("requests=%d result=%+v", requests.Load(), got.result)
	}
}

func TestRootRetryDeadlineInterruptsBackoff(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()

	runner := newRunner(t, newHTTPClient(t, 2), Config{SitemapURL: server.URL, MaxURLs: 50_000, MaxIndexChildren: 1, RootMaxAttempts: 2, RootBackoff: time.Hour})
	runner.sleep = func(context.Context, time.Duration) error { return context.DeadlineExceeded }
	result, err := runner.Run(context.Background())
	var sitemapErr *Error
	if !errors.As(err, &sitemapErr) || sitemapErr.Kind != ErrorDeadline || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("error=%v", err)
	}
	if requests.Load() != 1 || result.TransportMetrics.Requests != 1 || len(result.URLs) != 0 {
		t.Fatalf("requests=%d result=%+v", requests.Load(), result)
	}
}

func TestRootDoesNotRetryTerminalStatuses(t *testing.T) {
	for _, status := range []int{http.StatusBadRequest, http.StatusPaymentRequired, http.StatusNotFound, http.StatusGone} {
		t.Run(fmt.Sprintf("status=%d", status), func(t *testing.T) {
			var requests atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				requests.Add(1)
				w.WriteHeader(status)
			}))
			defer server.Close()
			runner := newRunner(t, newHTTPClient(t, 3), Config{SitemapURL: server.URL, MaxURLs: 50_000, MaxIndexChildren: 1, RootMaxAttempts: 3, RootBackoff: time.Millisecond})
			var sleeps []time.Duration
			noWait(runner, &sleeps)
			result, err := runner.Run(context.Background())
			var sitemapErr *Error
			if !errors.As(err, &sitemapErr) || sitemapErr.Kind != ErrorStatus || sitemapErr.Status != status {
				t.Fatalf("error=%v", err)
			}
			if requests.Load() != 1 || result.TransportMetrics.Requests != 1 || len(sleeps) != 0 || len(result.URLs) != 0 {
				t.Fatalf("requests=%d sleeps=%v result=%+v", requests.Load(), sleeps, result)
			}
		})
	}
}

func TestRootDoesNotRetryBoundErrors(t *testing.T) {
	for _, test := range []struct {
		name         string
		maxBody      int64
		maxAggregate int64
		wantKind     boundedhttp.ErrorKind
	}{
		{name: "body", maxBody: 8, maxAggregate: 64, wantKind: boundedhttp.ErrorBodyLimit},
		{name: "aggregate", maxBody: 64, maxAggregate: 8, wantKind: boundedhttp.ErrorAggregateLimit},
	} {
		t.Run(test.name, func(t *testing.T) {
			var requests atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				requests.Add(1)
				_, _ = w.Write([]byte(strings.Repeat("x", 32)))
			}))
			defer server.Close()
			runner := newRunner(t, newHTTPClientWithLimits(t, 3, time.Second, test.maxBody, test.maxAggregate), Config{SitemapURL: server.URL, MaxURLs: 50_000, MaxIndexChildren: 1, RootMaxAttempts: 3, RootBackoff: time.Millisecond})
			var sleeps []time.Duration
			noWait(runner, &sleeps)
			result, err := runner.Run(context.Background())
			var boundedErr *boundedhttp.Error
			if !errors.As(err, &boundedErr) || boundedErr.Kind != test.wantKind {
				t.Fatalf("error=%v", err)
			}
			if requests.Load() != 1 || result.TransportMetrics.Requests != 1 || len(sleeps) != 0 || len(result.URLs) != 0 {
				t.Fatalf("requests=%d sleeps=%v result=%+v", requests.Load(), sleeps, result)
			}
		})
	}
}
