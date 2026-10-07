package worker

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

const testDOMRows = `{"row_selector":"article","link_selector":"a","description_selector":".description","default_locations":["Zurich"]}`

func TestDOMRichRowsRetainFullBodyPaginationAndPythonURLJoining(t *testing.T) {
	config := map[string]string{"crawler_type": "dom", "board_url": "https://example.com/careers", "metadata": `{"rich_rows":` + testDOMRows + `,"pagination":{"param_name":"page","max_pages":4}}`}
	requests := 0
	client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		requests++
		body := strings.Repeat(" ", 500_010) + `<article><a href="/jobs/工程師">First title</a><div class="description"><p>First body</p></div></article>`
		if r.URL.Query().Get("page") == "2" {
			body = `<article><a href="/jobs/工程師">Later title</a><div class="description"><p>Later body</p></div></article><article><a href="/jobs/two">Second</a><div class="description"><p>Second body</p></div></article>`
		}
		if r.URL.Query().Get("page") == "3" {
			body = `<html><p>No further jobs</p></html>`
		}
		return &http.Response{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
	})}
	profile := queue.GreenhouseMonitorProfile{Provider: "dom", Profile: "dom.direct-rows/v1", Endpoint: config["board_url"]}
	got, err := discoverDOMInventory(context.Background(), client, profile, config)
	if err != nil || requests != 3 || len(got.Jobs) != 2 || got.Truncated || got.Jobs[0].URL != "https://example.com/jobs/工程師" || *got.Jobs[0].Title != "First title" || *got.Jobs[0].Description != `<div class="description"><p>First body</p></div>` {
		t.Fatal("rich pagination/body/URL semantics differ", got, requests, err)
	}
}

func TestRenderedRichRowsJoinAgainstFinalPageUsingPythonSemantics(t *testing.T) {
	rows, err := dom.RichRowsOptions([]byte(testDOMRows))
	if err != nil {
		t.Fatal(err)
	}
	profile := queue.GreenhouseMonitorProfile{Provider: "dom", Profile: "dom.rendered-rows/v1", Endpoint: "https://example.com/careers"}
	source := `<base href="https://ignored.example/"><article><a href="工程師?q=a b">Engineer</a><div class="description"><p>Build</p></div></article>`
	got, err := parseDOMInventory(context.Background(), RichDiscovery{}, profile, dom.ListingConfig{RichRows: rows}, source, "https://example.com/redirected/listing", true)
	if err != nil || len(got.Jobs) != 1 || got.Jobs[0].URL != "https://example.com/redirected/工程師?q=a b" {
		t.Fatal("rendered rich rows used browser href serialization", got, err)
	}
}
