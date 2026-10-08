package worker

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"sort"
	"strings"
	"sync"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func FetchUnifrHTTP(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, wait func(context.Context, time.Duration) error) (RichDiscovery, error) {
	out := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := api.UnifrOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil || client == nil || wait == nil || p.Provider != "unifr" || p.Profile != "unifr.authoritative-items/v1" || p.Endpoint != o.URL || config["monitor_needs_browser"] != "0" {
		return out, queue.ErrConfiguration
	}
	sealed := *client
	sealed.Jar, err = cookiejar.New(nil)
	if err != nil {
		return out, err
	}
	sealed.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	var observations sync.Mutex
	jobs, err := api.DiscoverUnifr(ctx, o, time.Now().UTC(), func(ctx context.Context, resource, kind string) ([]byte, error) {
		for attempt := 0; attempt < 3; attempt++ {
			raw, response, err := fetchProviderResource(ctx, &sealed, o, resource, nil, nil, 2_000_000)
			status := 0
			if response != nil {
				status = response.status
				observations.Lock()
				// Concurrent locale details may finish after a reserved resource.
				// Keep that evidence monotonic for canonical publisher settlement.
				if out.Response == nil || !out.Response.reserved {
					out.Response = response
				}
				observations.Unlock()
			}
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			var reserved *policy.Reservation
			if errors.As(err, &reserved) {
				return nil, err
			}
			if err == nil && status == 200 && len(strings.TrimSpace(string(raw))) > 0 {
				media := strings.ToLower(strings.TrimSpace(strings.Split(response.contentType, ";")[0]))
				if kind == "html" && media != "text/html" && media != "application/xhtml+xml" || kind == "json" && media != "application/json" && !strings.HasSuffix(media, "+json") {
					return nil, api.ErrInventory
				}
				return []byte(jsonld.DecodeDocument(raw, response.contentType)), nil
			}
			retry := status == 0 || status == 200 || status == 408 || status == 425 || status == 429 || status >= 500 && status <= 599
			if !retry || attempt == 2 {
				if err != nil {
					return nil, err
				}
				return nil, &DiscoveryError{Kind: "unifr_resource_failed", Status: status}
			}
			delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
			if err := wait(ctx, delay); err != nil {
				return nil, err
			}
		}
		return nil, api.ErrInventory
	})
	if err != nil {
		return out, err
	}
	for _, j := range jobs {
		if j.URLOnly {
			out.Jobs = append(out.Jobs, RichMonitorJob{URL: j.URL, URLOnly: true})
			continue
		}
		fields := map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "metadata": j.Metadata, "extras": j.Extras, "language": j.Language}
		if j.DatePosted != "" {
			fields["date_posted"] = j.DatePosted
		}
		job, err := secondaryRichJob(fields)
		if err != nil {
			return RichDiscovery{Response: out.Response}, err
		}
		locales := make([]string, 0, len(j.Localizations))
		for locale := range j.Localizations {
			locales = append(locales, locale)
		}
		sort.Strings(locales)
		for _, locale := range locales {
			job.LocalizationLocales = append(job.LocalizationLocales, locale)
			if title, ok := j.Localizations[locale]["title"].(string); ok {
				job.LocalizedTitles = append(job.LocalizedTitles, title)
			}
		}
		out.Jobs = append(out.Jobs, job)
	}
	return out, ctx.Err()
}
