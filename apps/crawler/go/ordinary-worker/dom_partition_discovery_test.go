package worker

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type partitionOriginalCase struct {
	Name     string
	BoardURL string `json:"board_url"`
	Metadata map[string]any
	Pages    map[string]struct {
		Status int
		Body   string
	}
	Expected  []string
	Error     bool
	ErrorType string `json:"error_type"`
}
type partitionTestTransport func(*http.Request) (*http.Response, error)

func (f partitionTestTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func partitionOriginalCases(t *testing.T) []partitionOriginalCase {
	t.Helper()
	raw, e := os.ReadFile("testdata/python_dom_partitions.json")
	var rows []partitionOriginalCase
	if e != nil || json.Unmarshal(raw, &rows) != nil || len(rows) != 12 {
		t.Fatal("original complete/failure partition corpus required")
	}
	return rows
}
func partitionOriginalConfig(t *testing.T, row partitionOriginalCase) (map[string]string, queue.GreenhouseMonitorProfile, dom.ListingConfig) {
	t.Helper()
	body, _ := json.Marshal(row.Metadata)
	config := map[string]string{"board_url": row.BoardURL, "crawler_type": "dom", "monitor_needs_browser": "0", "metadata": string(body), "company_id": "00000000-0000-4000-8000-000000000002", "board_slug": "partition-fixture", "check_interval_minutes": "60", "scrape_interval_hours": "24", "domain": "dom", "throttle_key": "dom", "scraper_needs_browser": "0"}
	p, e := queue.InspectRichMonitor("00000000-0000-4000-8000-000000000001", config)
	if e != nil {
		t.Fatal("partition ownership rejected")
	}
	c, e := queue.DOMMonitorOptions(config)
	if e != nil {
		t.Fatal(e)
	}
	return config, p, c
}

func TestDOMPartitionsOriginalSnapshots(t *testing.T) {
	for _, row := range partitionOriginalCases(t) {
		t.Run(row.Name, func(t *testing.T) {
			config, p, c := partitionOriginalConfig(t, row)
			var roots, inFlight, maximum atomic.Int32
			client := &http.Client{Transport: partitionTestTransport(func(r *http.Request) (*http.Response, error) {
				n := inFlight.Add(1)
				defer inFlight.Add(-1)
				for old := maximum.Load(); n > old; old = maximum.Load() {
					if maximum.CompareAndSwap(old, n) {
						break
					}
				}
				if r.URL.String() == row.BoardURL {
					roots.Add(1)
				} else if r.Header.Get("Cookie") != "" {
					t.Error("stateless facet inherited root cookie")
				}
				select {
				case <-r.Context().Done():
					return nil, r.Context().Err()
				case <-time.After(time.Millisecond):
				}
				v, ok := row.Pages[r.URL.String()]
				if !ok {
					v.Status = 404
					v.Body = "missing"
				}
				h := http.Header{"Content-Type": []string{"text/html; charset=utf-8"}, "Set-Cookie": []string{"sticky=root; Path=/"}}
				return &http.Response{StatusCode: v.Status, Header: h, Body: io.NopCloser(strings.NewReader(v.Body)), ContentLength: int64(len(v.Body)), Request: r}, nil
			})}
			found, e := discoverDOMPartitions(context.Background(), client, p, config, c, func(context.Context, time.Duration) error { return nil })
			if row.Error {
				if e == nil || len(found.Jobs) != 0 {
					t.Fatal("failed snapshot returned a publishable inventory")
				}
				if row.ErrorType == "_PartitionSnapshotChanged" && (!errors.Is(e, dom.ErrPartitionSnapshot) || roots.Load() != 4) {
					t.Fatal("count mismatch did not retry four complete snapshots")
				}
			} else {
				if e != nil {
					t.Fatal(e)
				}
				urls := []string{}
				for _, job := range found.Jobs {
					urls = append(urls, job.URL)
				}
				sort.Strings(urls)
				if !reflect.DeepEqual(urls, row.Expected) {
					t.Fatalf("original inventory differs: got %v want %v", urls, row.Expected)
				}
				if roots.Load() != 1 {
					t.Fatal("healthy snapshot retried")
				}
			}
			if maximum.Load() > 4 {
				t.Fatal("partition HTTP concurrency exceeded original bound")
			}
		})
	}
}

func TestDOMPartitionsLatePublisherReservation(t *testing.T) {
	row := partitionOriginalCases(t)[0]
	config, p, c := partitionOriginalConfig(t, row)
	client := &http.Client{Transport: partitionTestTransport(func(r *http.Request) (*http.Response, error) {
		v := row.Pages[r.URL.String()]
		headers := http.Header{"Content-Type": []string{"text/html"}}
		if strings.Contains(r.URL.RawQuery, "facet=b&page=2") {
			headers.Set("TDM-Reservation", "1")
		}
		return &http.Response{StatusCode: v.Status, Header: headers, Body: io.NopCloser(strings.NewReader(v.Body)), Request: r}, nil
	})}
	found, e := discoverDOMPartitions(context.Background(), client, p, config, c, func(context.Context, time.Duration) error { return nil })
	if e == nil || len(found.Jobs) != 0 || found.Response == nil || !found.Response.reserved || !queue.DOMMonitorResourceMatches(p, config, found.Response.endpoint) {
		t.Fatal("late publisher reservation lost its inventory authority")
	}
}
