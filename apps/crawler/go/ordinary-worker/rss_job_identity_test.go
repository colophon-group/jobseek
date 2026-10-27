package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path"
	"reflect"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRSSJobInviteIdentityPacesPublisherRequests(t *testing.T) {
	var mu sync.Mutex
	var last time.Time
	var blocked bool
	calls := 0
	client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		calls++
		now := time.Now()
		if !last.IsZero() && now.Sub(last) < 75*time.Millisecond {
			blocked = true
		}
		last = now
		if blocked {
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}
		w.Header().Set("Location", "/job/Resolved/"+path.Base(r.URL.Path)+"-en_GB/")
		w.WriteHeader(http.StatusFound)
	})
	p := queue.GreenhouseMonitorProfile{Provider: "rss", Endpoint: "https://example.com/googlefeed.xml", RSSResolveJobInviteIdentity: true}
	input := RichDiscovery{}
	for i := 1; i <= 4; i++ {
		title := "Retained title"
		input.Jobs = append(input.Jobs, RichMonitorJob{URL: fmt.Sprintf("https://example.com/job/Alias/%d/", i), Title: &title, Metadata: map[string]any{"id": fmt.Sprint(i), "company": "Retained company"}})
	}
	out, err := resolveRSSJobInviteIdentities(context.Background(), client.client, p, input)
	mu.Lock()
	finalBlocked, finalCalls := blocked, calls
	mu.Unlock()
	if err != nil || len(out.Jobs) != len(input.Jobs) || finalBlocked || finalCalls != len(input.Jobs) {
		t.Fatal("paced identity lookup did not return the complete inventory", err, finalCalls)
	}
	for i, job := range out.Jobs {
		id := fmt.Sprint(i + 1)
		if job.URL != "https://example.com/job/Resolved/"+id+"-en_GB/" || job.Title == nil || *job.Title != "Retained title" || job.Metadata["feed_id"] != id || job.Metadata["job_invite_id"] != id || job.Metadata["company"] != "Retained company" {
			t.Fatal("identity resolution changed retained content or identity")
		}
		if input.Jobs[i].URL != fmt.Sprintf("https://example.com/job/Alias/%d/", i+1) || len(input.Jobs[i].Metadata) != 2 {
			t.Fatal("identity resolution mutated the caller inventory")
		}
	}
}

type rssIdentityTestTransport func(*http.Request) (*http.Response, error)

func (f rssIdentityTestTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestRSSJobInviteIdentityPacingCancellationStopsBeforeRequest(t *testing.T) {
	var calls atomic.Int32
	p := rssIdentityPacedTransport{interval: rssIdentityRequestInterval, next: time.Now().Add(time.Hour), inner: rssIdentityTestTransport(func(*http.Request) (*http.Response, error) {
		calls.Add(1)
		return nil, errors.New("unexpected request")
	})}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodHead, "https://example.com/job/Alias/7/", nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = p.RoundTrip(request); !errors.Is(err, context.DeadlineExceeded) || calls.Load() != 0 {
		t.Fatal("cancelled pacing reached the publisher", err)
	}
}

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
