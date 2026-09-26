package sitemap

import (
	"context"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor/boundedhttp"
)

func TestProductionURLSetResultAndIndexFailsClosed(t *testing.T) {
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.URL.Path == "/index.xml" {
			_, _ = w.Write([]byte(`<sitemapindex><sitemap><loc>https://example.test/child.xml</loc></sitemap></sitemapindex>`))
			return
		}
		_, _ = w.Write([]byte(`<urlset><url><loc>https://example.test/job/1?utm_source=x&amp;id=1</loc></url><url><loc>https://example.test/job/2</loc></url></urlset>`))
	}))
	defer server.Close()
	client, err := boundedhttp.New(boundedhttp.Config{
		RequestTimeout:           time.Second,
		MaxDecodedBodyBytes:      1024,
		MaxRequests:              3,
		MaxAggregateDecodedBytes: 2048,
		AllowPrivateNetwork:      true,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	config := Config{
		SitemapURL:          server.URL + "/sitemap.xml",
		MaxURLs:             50_000,
		MaxIndexChildren:    1,
		RequireURLSet:       true,
		AllowHTTPForTesting: true,
	}
	runner, err := New(client, config)
	if err != nil {
		t.Fatal(err)
	}
	result, err := runner.Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"https://example.test/job/1?id=1", "https://example.test/job/2"}
	if !reflect.DeepEqual(result.URLs, want) || result.TransportMetrics.Requests != 1 || result.TransportMetrics.Responses != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}
	config.SitemapURL = server.URL + "/index.xml"
	runner, err = New(client, config)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := runner.Run(context.Background()); err == nil {
		t.Fatal("index must fail closed for this production profile")
	}
	if requests != 2 {
		t.Fatalf("unexpected origin requests: %d", requests)
	}
}
