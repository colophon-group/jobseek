package worker

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestStaticDOMListingRetainsFrozenPythonURLsAndFilters(t *testing.T) {
	body, err := os.ReadFile("testdata/python_dom_listings.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixtures struct {
		Joins    []struct{ Base, Href, Want string }
		Listings []struct {
			HTML, Base string
			Config     map[string]any
			URLs       []string
		}
	}
	if err := json.Unmarshal(body, &fixtures); err != nil {
		t.Fatal(err)
	}
	for _, c := range fixtures.Joins {
		got, ok := joinPythonURL(c.Base, c.Href)
		if !ok || got != c.Want {
			t.Fatalf("join %q: got %q, want %q", c.Href, got, c.Want)
		}
	}
	for _, c := range fixtures.Listings {
		md, _ := json.Marshal(c.Config)
		config := map[string]string{"crawler_type": "dom", "board_url": c.Base, "metadata": string(md)}
		client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
			return &http.Response{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(c.HTML)), Request: r}, nil
		})}
		result, err := discoverDOMInventory(context.Background(), client, queue.GreenhouseMonitorProfile{Provider: "dom", Profile: "dom.direct-urls/v1", Endpoint: c.Base}, config)
		if err != nil {
			t.Fatal(err)
		}
		// The frozen URLs are the original shared monitor's dispatched result.
		// DOM discovery verifies every included URL before shared exclusions.
		result.Jobs, err = applyFeedMonitorURLs(context.Background(), config, result.Jobs)
		if err != nil {
			t.Fatal(err)
		}
		got := []string{}
		for _, job := range result.Jobs {
			got = append(got, job.URL)
		}
		if !reflect.DeepEqual(got, c.URLs) {
			t.Fatalf("listing URLs differ: got %q, want %q", got, c.URLs)
		}
	}
}
