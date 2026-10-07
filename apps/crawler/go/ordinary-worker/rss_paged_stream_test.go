package worker

import (
	"context"
	"encoding/json"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"os"
	"reflect"
	"testing"
)

func TestRSSPagedStreamMatchesActualPythonAcrossPageBoundaries(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_rss_paged_stream.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name             string
		Metadata         map[string]any
		Bodies, Requests []string
		Batches          [][]string
		Error            *string
	}
	if err = json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) != 13 {
		t.Fatal("incomplete actual Python pagination reference")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			md, _ := json.Marshal(c.Metadata)
			preset := c.Metadata["preset"].(string)
			p, err := queue.RSSPaginationOptions(string(md), preset)
			if err != nil {
				t.Fatal(err)
			}
			requests := []string{}
			result, err := collectRSSPages(context.Background(), c.Metadata["feed_url"].(string), p, false, func(_ context.Context, endpoint string) (RichDiscovery, error) {
				requests = append(requests, endpoint)
				if len(requests) > len(c.Bodies) {
					t.Fatal("fetched beyond actual Python traversal")
				}
				body := []byte(c.Bodies[len(requests)-1])
				if _, summary := c.Metadata["description_mode"]; summary {
					return parseGenericSummaryPage(body, false)
				}
				return parseRSSProviderPage(body, "generic", true, false)
			})
			if (err != nil) != (c.Error != nil) || !reflect.DeepEqual(requests, c.Requests) {
				t.Fatal("traversal/failure differs", err, c.Error, requests, c.Requests)
			}
			got := []string{}
			want := []string{}
			for _, job := range result.Jobs {
				got = append(got, job.URL)
			}
			for _, batch := range c.Batches {
				want = append(want, batch...)
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatal("original cross-page batch prefix differs", len(got), len(want))
			}
			if c.Error != nil {
				requests = nil
				result, err = collectRSSPages(context.Background(), c.Metadata["feed_url"].(string), p, true, func(_ context.Context, endpoint string) (RichDiscovery, error) {
					requests = append(requests, endpoint)
					body := []byte(c.Bodies[len(requests)-1])
					if _, summary := c.Metadata["description_mode"]; summary {
						return parseGenericSummaryPage(body, false)
					}
					return parseRSSProviderPage(body, "generic", true, false)
				})
				if err == nil || len(result.Jobs) != 0 {
					t.Fatal("failed rendered attempt leaked atomic batches", result, err)
				}
			}
		})
	}
}
