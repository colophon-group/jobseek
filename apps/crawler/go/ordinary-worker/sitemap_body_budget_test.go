package worker

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"

	bounded "github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor/boundedhttp"
)

type sitemapBudgetTransport struct{ body string }

func (r sitemapBudgetTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	return &http.Response{StatusCode: 200, Header: make(http.Header), Request: req, Body: io.NopCloser(strings.NewReader(r.body))}, nil
}

func TestSitemapLargeIndexAggregateBudgetAndExhaustion(t *testing.T) {
	const resource = "https://example.com/jobs.xml"
	const body = `<urlset><url><loc>https://example.com/job/1</loc></url></urlset>`
	for _, c := range []struct {
		name  string
		prior int64
		want  bounded.ErrorKind
	}{
		{"beyond_old_single_file_budget", 56 << 20, ""},
		{"aggregate_exhausted", 512 << 20, bounded.ErrorAggregateLimit},
		{"last_leaf_exceeds_remaining", (512 << 20) - 10, bounded.ErrorBodyLimit},
	} {
		t.Run(c.name, func(t *testing.T) {
			s := &nativeSitemapSession{client: &http.Client{Transport: sitemapBudgetTransport{body: body}}, endpoint: resource, stats: bounded.Stats{DecodedBytes: c.prior}}
			r, err := s.Get(context.Background(), resource, make(http.Header))
			if c.want == "" {
				if err != nil || string(r.Body) != body || s.stats.DecodedBytes != c.prior+int64(len(body)) {
					t.Fatal(r, err, s.stats)
				}
				return
			}
			var limit *bounded.Error
			if !errors.As(err, &limit) || limit.Kind != c.want || len(r.Body) != 0 {
				t.Fatal(r, err)
			}
		})
	}
}
