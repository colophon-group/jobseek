package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

const domVerificationJob = `<script type="application/ld+json">{"@type":"JobPosting","title":"Engineer"}</script>`

func TestOriginalDOMIncludeBoardAndJSONLDVerificationHTTP(t *testing.T) {
	raw, e := os.ReadFile("../dom-detail/testdata/python_listing_verification.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name     string
		BoardURL string `json:"board_url"`
		Metadata json.RawMessage
		Listing  string
		Details  map[string]struct {
			Status int
			Body   string
		}
		Expected, Calls    []string
		ProcessingExpected []string       `json:"processing_expected"`
		DropReasons        map[string]int `json:"drop_reasons"`
		Error              bool
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 16 {
		t.Fatal("actual Python DOM oracle missing")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			config := map[string]string{"crawler_type": "dom", "board_url": c.BoardURL, "metadata": string(c.Metadata)}
			p := queue.GreenhouseMonitorProfile{Provider: "dom", Profile: "dom.direct-urls/v1", Endpoint: c.BoardURL}
			var mu sync.Mutex
			calls := []string{}
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				mu.Lock()
				calls = append(calls, r.URL.String())
				mu.Unlock()
				body, status := c.Listing, 200
				if d, ok := c.Details[r.URL.Path]; ok {
					body, status = d.Body, d.Status
				}
				return &http.Response{StatusCode: status, Header: http.Header{"Content-Type": []string{"text/html; charset=utf-8"}}, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})}
			found, e := discoverDOMInventory(context.Background(), client, p, config)
			if (e != nil) != c.Error {
				t.Fatal("original outcome changed", e, c.Error)
			}
			sort.Strings(calls)
			sort.Strings(c.Calls)
			if !reflect.DeepEqual(calls, c.Calls) {
				t.Fatal("original request count/resources changed", calls, c.Calls)
			}
			if e != nil {
				if len(found.Jobs) != 0 {
					t.Fatal("failed verification retained partial jobs")
				}
				return
			}
			urls := []string{}
			for _, j := range found.Jobs {
				urls = append(urls, j.URL)
			}
			sort.Strings(urls)
			if !reflect.DeepEqual(urls, c.Expected) {
				t.Fatal("original verified inventory changed", urls, c.Expected)
			}
			normalized, e := NormalizeRichInventory(context.Background(), c.BoardURL, found.Jobs, false)
			if e != nil {
				t.Fatal(e)
			}
			processed := []string{}
			for _, j := range normalized.Jobs {
				processed = append(processed, j.URL)
			}
			sort.Strings(processed)
			if !reflect.DeepEqual(processed, c.ProcessingExpected) || !reflect.DeepEqual(normalized.DropReasons, c.DropReasons) {
				t.Fatal("original processing classification differs", processed, c.ProcessingExpected, normalized.DropReasons, c.DropReasons)
			}
		})
	}
}
func TestDOMVerificationPublisherPrecedenceAndExactResourceBinding(t *testing.T) {
	for _, status := range []int{200, 404, 410, 503} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			p := queue.GreenhouseMonitorProfile{Provider: "dom", Profile: "dom.direct-urls/v1", Endpoint: "https://example.com/careers", BoardID: "fixture", EffectiveConfigSHA256: "bound"}
			config := map[string]string{"crawler_type": "dom", "board_url": p.Endpoint, "metadata": `{"require_jsonld_jobposting":true}`}
			calls := 0
			source := "https://detail.example.com/jobs/1"
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				return &http.Response{StatusCode: status, Header: http.Header{"Tdm-Reservation": []string{"1"}}, Body: io.NopCloser(strings.NewReader("bad")), Request: r}, nil
			})}
			found, e := verifyDOMJobPostingInventoryWithWait(context.Background(), client, p, config, RichDiscovery{Jobs: []RichMonitorJob{{URL: source, URLOnly: true}}}, noSecondaryWait)
			if e == nil || calls != 1 || len(found.Jobs) != 0 || found.Response == nil || !found.Response.reserved || !domVerificationResponseMatches(p, config, found.Response) {
				t.Fatal("publisher signal swallowed/unbound", e, calls)
			}
			other := p
			other.BoardID = "foreign"
			if domVerificationResponseMatches(other, config, found.Response) {
				t.Fatal("foreign board reused proof")
			}
			changed := map[string]string{}
			for k, v := range config {
				changed[k] = v
			}
			changed["metadata"] = `{"require_jsonld_jobposting":false}`
			if domVerificationResponseMatches(p, changed, found.Response) {
				t.Fatal("changed verification config reused proof")
			}
			forged := *found.Response
			forged.endpoint = "https://detail.example.com/unobserved"
			if domVerificationResponseMatches(p, config, &forged) {
				t.Fatal("unobserved resource reused proof")
			}
		})
	}
}
func TestDOMVerificationNoPreviewTruncationAndBoundedFailureDrain(t *testing.T) {
	p := queue.GreenhouseMonitorProfile{Provider: "dom", Profile: "dom.direct-urls/v1", Endpoint: "https://example.com/careers"}
	config := map[string]string{"crawler_type": "dom", "board_url": p.Endpoint, "metadata": `{"require_jsonld_jobposting":true}`}
	client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(strings.Repeat("x", 500100) + domVerificationJob)), Request: r}, nil
	})}
	found, e := verifyDOMJobPostingInventoryWithWait(context.Background(), client, p, config, RichDiscovery{Jobs: []RichMonitorJob{{URL: "https://example.com/jobs/1"}}}, noSecondaryWait)
	if e != nil || len(found.Jobs) != 1 {
		t.Fatal("verification truncated before authoritative JSON-LD", e)
	}
	jobs := []RichMonitorJob{}
	for i := 0; i < 501; i++ {
		jobs = append(jobs, RichMonitorJob{URL: fmt.Sprintf("https://example.com/jobs/%03d", i)})
	}
	requests := atomic.Int32{}
	client.Transport = workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		requests.Add(1)
		return nil, fmt.Errorf("should not request")
	})
	if found, e = verifyDOMJobPostingInventoryWithWait(context.Background(), client, p, config, RichDiscovery{Jobs: jobs}, noSecondaryWait); e == nil || requests.Load() != 0 || len(found.Jobs) != 0 {
		t.Fatal("verification cap ignored")
	}
	var active, peak atomic.Int32
	allStarted := make(chan struct{})
	var once sync.Once
	client.Transport = workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		n := active.Add(1)
		defer active.Add(-1)
		for n > peak.Load() {
			peak.CompareAndSwap(peak.Load(), n)
		}
		if n == 8 {
			once.Do(func() { close(allStarted) })
		}
		select {
		case <-allStarted:
		case <-r.Context().Done():
			return nil, r.Context().Err()
		}
		if r.URL.Path == "/jobs/000" {
			return &http.Response{StatusCode: 503, Header: http.Header{}, Body: io.NopCloser(strings.NewReader("unavailable")), Request: r}, nil
		}
		<-r.Context().Done()
		return nil, r.Context().Err()
	})
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if found, e = verifyDOMJobPostingInventoryWithWait(ctx, client, p, config, RichDiscovery{Jobs: jobs[:20]}, noSecondaryWait); e == nil || len(found.Jobs) != 0 || active.Load() != 0 || peak.Load() != 8 {
		t.Fatal("failed verification exceeded concurrency or leaked siblings", e, active.Load(), peak.Load())
	}
}
