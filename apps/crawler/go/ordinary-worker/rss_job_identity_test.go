package worker

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"reflect"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRSSJobInviteIdentityMatchesOriginalPython(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_sf_job_identity.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Cases []struct {
			Name, Source, Location string
			Statuses               []int
			Calls                  []map[string]string
			Error                  bool
			ExpectedURL            string         `json:"expected_url"`
			ExpectedMetadata       map[string]any `json:"expected_metadata"`
		}
	}
	if err = json.Unmarshal(raw, &corpus); err != nil || len(corpus.Cases) != 14 {
		t.Fatal(err)
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			calls := []map[string]string{}
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls = append(calls, map[string]string{"method": r.Method, "url": "https://" + r.Host + r.URL.String()})
				if c.Location != "" {
					w.Header().Set("Location", c.Location)
				}
				w.WriteHeader(c.Statuses[min(len(calls)-1, len(c.Statuses)-1)])
			})
			p := queue.GreenhouseMonitorProfile{Provider: "rss", Endpoint: "https://example.com/googlefeed.xml", RSSResolveJobInviteIdentity: true}
			input := RichDiscovery{Jobs: []RichMonitorJob{{URL: c.Source, Metadata: map[string]any{"id": "feed-7", "company": "Main"}}}}
			out, err := resolveRSSJobInviteIdentities(context.Background(), client.client, p, input)
			if (err != nil) != c.Error || !reflect.DeepEqual(calls, c.Calls) {
				t.Fatal(err, calls, c.Calls)
			}
			if c.Error {
				if len(out.Jobs) != 0 {
					t.Fatal("failed identity published inventory")
				}
				return
			}
			if out.Jobs[0].URL != c.ExpectedURL || !reflect.DeepEqual(out.Jobs[0].Metadata, c.ExpectedMetadata) {
				t.Fatal(out.Jobs[0], c.ExpectedMetadata)
			}
			if input.Jobs[0].URL != c.Source || len(input.Jobs[0].Metadata) != 2 {
				t.Fatal("resolver mutated caller inventory")
			}
		})
	}
}

func TestRSSJobInviteIdentityRejectsIncompleteAndPublisherReserved(t *testing.T) {
	p := queue.GreenhouseMonitorProfile{Provider: "rss", Endpoint: "https://example.com/googlefeed.xml", RSSResolveJobInviteIdentity: true}
	client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("TDM-Reservation", "1")
		w.WriteHeader(302)
	})
	for _, input := range []RichDiscovery{
		{Truncated: true}, {Jobs: make([]RichMonitorJob, 2001)},
		{Jobs: []RichMonitorJob{{URL: "https://example.com/job/Alias/7/"}}},
	} {
		out, err := resolveRSSJobInviteIdentities(context.Background(), client.client, p, input)
		if err == nil || len(out.Jobs) != 0 {
			t.Fatal("incomplete inventory published", out, err)
		}
		if len(input.Jobs) == 1 && (out.Response == nil || !out.Response.reserved) {
			t.Fatal("publisher reservation lost", out, err)
		}
	}
}

func TestRSSJobInviteIdentityAcceptsUnicodeLocationWithoutChangingOriginalURL(t *testing.T) {
	location := "/job/München-Projektleiter-Rohbau-Großprojekt-(mwd)/13351-de_DE/"
	client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Location", location)
		w.WriteHeader(http.StatusFound)
	})
	p := queue.GreenhouseMonitorProfile{Provider: "rss", Endpoint: "https://example.com/googlefeed.xml", RSSResolveJobInviteIdentity: true}
	input := RichDiscovery{Jobs: []RichMonitorJob{{URL: "https://example.com/job/Alias/1364692957/", Metadata: map[string]any{"id": "feed-7"}}}}
	out, err := resolveRSSJobInviteIdentities(context.Background(), client.client, p, input)
	if err != nil || len(out.Jobs) != 1 || out.Jobs[0].URL != "https://example.com"+location || out.Jobs[0].Metadata["job_invite_id"] != "13351" || out.Jobs[0].Metadata["job_locale"] != "de_DE" || out.Jobs[0].Metadata["feed_id"] != "feed-7" {
		t.Fatal("original Unicode redirect identity changed", err)
	}
}
