package worker

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/cookiejar"
	"strings"
	"sync"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

var errMokahrSnapshotChanged = errors.New("MokaHR inventory changed during pagination")

func fetchMokahrResource(ctx context.Context, client *http.Client, options api.MokahrOptions, endpoint string, body []byte) ([]byte, *GreenhouseResponse, error) {
	return fetchProviderResource(ctx, client, options, endpoint, body, nil, 64<<20)
}

type mokahrPartitionResult struct {
	Jobs      []api.MokahrJob
	Truncated bool
	Response  *GreenhouseResponse
}

func discoverMokahrPartition(ctx context.Context, client *http.Client, options api.MokahrOptions, p api.MokahrPartition) (mokahrPartitionResult, error) {
	result := mokahrPartitionResult{}
	raw, response, e := fetchMokahrResource(ctx, client, options, p.PageURL, nil)
	result.Response = response
	if e != nil {
		return result, e
	}
	page := jsonld.DecodeDocument(raw, "text/html")
	if api.MokahrClosedPage.MatchString(page) {
		result.Response.providerDisabled = true
		return result, &DiscoveryError{Kind: "provider_gone", Status: 200}
	}
	iv, cities, e := api.MokahrBootstrap(page, p)
	if e != nil {
		return result, e
	}
	fetch := func(offset, limit int) ([]map[string]any, int, error) {
		body, e := json.Marshal(map[string]any{"orgId": p.OrgID, "siteId": p.SiteID, "limit": limit, "offset": offset, "needStat": true, "locale": options.Locale})
		if e != nil {
			return nil, 0, e
		}
		raw, response, e := fetchMokahrResource(ctx, client, options, p.APIURL(), body)
		result.Response = response
		if e != nil {
			return nil, 0, e
		}
		d, e := api.Decode(raw)
		if e != nil {
			return nil, 0, e
		}
		return api.MokahrListing(d, iv, p)
	}
	expected, seen := -1, 0
	ids := map[string]bool{}
	for {
		limit := min(50, max(50000-seen, 1))
		rows, total, e := fetch(seen, limit)
		if e != nil {
			return result, e
		}
		if expected < 0 {
			expected = total
		} else if total != expected {
			return result, errMokahrSnapshotChanged
		}
		if expected == 0 {
			if len(rows) != 0 {
				return result, api.ErrInventory
			}
			confirm, count, e := fetch(0, 50)
			if e != nil {
				return result, e
			}
			if count != 0 || len(confirm) != 0 {
				return result, api.ErrInventory
			}
			return result, nil
		}
		if len(rows) == 0 || len(rows) > limit || len(rows) > expected-seen {
			return result, api.ErrInventory
		}
		for _, row := range rows {
			id, ok := row["id"].(string)
			if !ok || !validMokahrProviderID(id) || row["orgId"] != p.OrgID {
				return result, api.ErrInventory
			}
			if ids[id] {
				return result, errMokahrSnapshotChanged
			}
			ids[id] = true
			status, ok := row["status"].(string)
			if !ok {
				return result, api.ErrInventory
			}
			switch strings.ToLower(status) {
			case "open":
				j, e := api.MokahrProject(row, p, cities)
				if e != nil {
					return result, e
				}
				result.Jobs = append(result.Jobs, j)
			case "closed", "pause":
				if !validMokahrProviderID(id) {
					return result, api.ErrInventory
				}
			default:
				return result, api.ErrInventory
			}
		}
		seen += len(rows)
		if seen >= min(expected, 50000) {
			break
		}
		if len(rows) < limit {
			return result, api.ErrInventory
		}
	}
	result.Truncated = expected > 50000
	if !result.Truncated && seen != expected {
		return result, api.ErrInventory
	}
	return result, nil
}

func validMokahrProviderID(id string) bool {
	if len(id) < 1 || len(id) > 128 {
		return false
	}
	for _, c := range id {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-') {
			return false
		}
	}
	return true
}

// Partition preference is configuration order. A changed count/repeated ID
// restarts the complete union once; no failed prefix can reach canonical writes.
func discoverMokahrInventory(ctx context.Context, client *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	options, e := api.MokahrOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || client == nil || config["monitor_needs_browser"] != "0" || p.Provider != "mokahr" || p.Profile != "mokahr.encrypted-items/v1" || p.Endpoint != options.Partitions[0].PageURL {
		return RichDiscovery{}, queue.ErrConfiguration
	}
	operationClient := *client
	operationClient.Jar, e = cookiejar.New(nil)
	if e != nil {
		return RichDiscovery{}, e
	}
	client = &operationClient
	for attempt := 0; attempt < 2; attempt++ {
		result := RichDiscovery{Jobs: []RichMonitorJob{}}
		selected := map[string]bool{}
		var failure error
		operationCtx, cancel := context.WithCancel(ctx)
		partitions := make([]mokahrPartitionResult, len(options.Partitions))
		var mutex sync.Mutex
		var tasks sync.WaitGroup
		for index, source := range options.Partitions {
			tasks.Go(func() {
				found, e := discoverMokahrPartition(operationCtx, client, options, source)
				mutex.Lock()
				partitions[index] = found
				var reserved *policy.Reservation
				if e != nil && (failure == nil || errors.As(e, &reserved)) {
					failure = e
					result.Response = found.Response
					cancel()
				}
				mutex.Unlock()
			})
		}
		tasks.Wait()
		cancel()
		if failure != nil {
			if !errors.Is(failure, errMokahrSnapshotChanged) || attempt == 1 {
				return RichDiscovery{Response: result.Response}, failure
			}
			if e := pauseRich(ctx, time.Second); e != nil {
				return RichDiscovery{Response: result.Response}, e
			}
			continue
		}
		for _, found := range partitions {
			result.Response = found.Response
			result.Truncated = result.Truncated || found.Truncated
			for _, row := range found.Jobs {
				id, _ := row.Metadata["provider_id"].(string)
				if selected[id] {
					continue
				}
				if len(result.Jobs) >= 50000 {
					result.Truncated = true
					continue
				}
				selected[id] = true
				title, _ := row.Title.(string)
				j := RichMonitorJob{URL: row.URL, Title: &title, Locations: row.Locations, DatePosted: row.DatePosted, EmploymentType: row.EmploymentType, Metadata: row.Metadata, Extras: row.Extras}
				if s, ok := row.Description.(string); ok {
					body, e := enrichment.NormalizeDescriptionHTML(s)
					if e != nil {
						return RichDiscovery{Response: result.Response}, e
					}
					j.Description = body
				}
				result.Jobs = append(result.Jobs, j)
			}
		}
		return result, ctx.Err()
	}
	return RichDiscovery{}, api.ErrInventory
}
