package sitemap

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"reflect"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor/boundedhttp"
)

type indexSession struct {
	get   func(string, int) (boundedhttp.Response, error)
	calls map[string]int
}

func (s *indexSession) Get(_ context.Context, resource string, _ http.Header) (boundedhttp.Response, error) {
	s.calls[resource]++
	return s.get(resource, s.calls[resource])
}
func (s *indexSession) Stats() boundedhttp.Stats { return boundedhttp.Stats{} }

func TestCompleteNestedSitemapInventoryAndShardFailureBoundaries(t *testing.T) {
	for _, mode := range []string{"success", "empty_leaf", "missing", "malformed_child", "retry403", "retry408", "retry425", "exhausted503", "policy", "foreign", "depth", "child_limit"} {
		t.Run(mode, func(t *testing.T) {
			config := Config{SitemapURL: "https://example.com/index.xml", MaxURLs: 50_000, MaxIndexChildren: 200, MaxIndexDepth: 8, ChildMaxAttempts: 3, RootBackoff: time.Nanosecond}
			if mode == "depth" {
				config.MaxIndexDepth = 1
			}
			if mode == "child_limit" {
				config.MaxIndexChildren = 1
			}
			s := &indexSession{calls: map[string]int{}}
			s.get = func(resource string, attempt int) (boundedhttp.Response, error) {
				body := ""
				switch resource {
				case config.SitemapURL:
					body = `<sitemapindex><sitemap><loc>https://example.com/jobs-index.xml</loc></sitemap><sitemap><loc>https://example.com/marketing.xml</loc></sitemap></sitemapindex>`
				case "https://example.com/jobs-index.xml":
					body = `<sitemapindex><sitemap><loc>https://example.com/jobs-index.xml</loc></sitemap><sitemap><loc>https://example.com/index.xml</loc></sitemap><sitemap><loc>https://example.com/jobs-a.xml</loc></sitemap><sitemap><loc>https://example.com/jobs-b.xml</loc></sitemap><sitemap><loc>https://example.com/jobs-b.xml</loc></sitemap></sitemapindex>`
					if mode == "foreign" {
						body = `<sitemapindex><sitemap><loc>https://foreign.example/jobs.xml</loc></sitemap></sitemapindex>`
					}
				case "https://example.com/jobs-a.xml", "https://example.com/jobs-b.xml":
					if mode == "missing" {
						return boundedhttp.Response{StatusCode: 404}, nil
					}
					if mode == "empty_leaf" {
						body = `<urlset/>`
						break
					}
					if resource == "https://example.com/jobs-b.xml" {
						switch mode {
						case "malformed_child":
							body = `<urlset>`
						case "retry403", "retry408", "retry425":
							if attempt < 3 {
								status := map[string]int{"retry403": 403, "retry408": 408, "retry425": 425}[mode]
								return boundedhttp.Response{StatusCode: status}, nil
							}
						case "exhausted503":
							return boundedhttp.Response{StatusCode: 503}, nil
						case "policy":
							return boundedhttp.Response{}, &boundedhttp.Error{Kind: boundedhttp.ErrorTDMReservation}
						}
					}
					if body == "" {
						body = fmt.Sprintf(`<urlset><url><loc>https://example.com/job/%s?utm_source=fixture</loc></url></urlset>`, resource[len("https://example.com/jobs-"):len(resource)-len(".xml")])
					}
				default:
					t.Fatal("unexpected or repeated index resource", resource)
				}
				return boundedhttp.Response{StatusCode: 200, Body: []byte(body)}, nil
			}
			result, err := RunWithSession(context.Background(), config, s)
			failed := mode == "missing" || mode == "exhausted503" || mode == "policy" || mode == "foreign" || mode == "depth" || mode == "child_limit"
			if failed {
				if err == nil || len(result.URLs) != 0 {
					t.Fatal("incomplete index published inventory", result, err)
				}
				if mode == "policy" && !errors.As(err, new(*boundedhttp.Error)) {
					t.Fatal("policy lost error classification")
				}
			} else {
				want := []string{"https://example.com/job/a", "https://example.com/job/b"}
				if mode == "malformed_child" {
					want = want[:1]
				}
				if mode == "empty_leaf" {
					want = []string{}
				}
				if err != nil || !reflect.DeepEqual(result.URLs, want) {
					t.Fatal("complete union changed", result, err)
				}
			}
			if s.calls[config.SitemapURL] != 1 || s.calls["https://example.com/jobs-index.xml"] != 1 || s.calls["https://example.com/marketing.xml"] != 0 {
				t.Fatal("cycle, preference or duplicate fetch contract changed", s.calls)
			}
			wantCalls := 1
			if mode == "retry403" || mode == "retry408" || mode == "retry425" || mode == "exhausted503" {
				wantCalls = 3
			}
			if mode != "foreign" && mode != "depth" && mode != "child_limit" && s.calls["https://example.com/jobs-b.xml"] != wantCalls {
				t.Fatal("child retry budget changed", s.calls)
			}
		})
	}
}
