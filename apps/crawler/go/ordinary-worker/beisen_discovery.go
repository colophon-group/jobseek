package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"strconv"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

var beisenDisplayFields = []string{"Category", "Kind", "LocId", "DetailAddress", "Org", "Degree", "YearsOfWorking", "Salary", "PostDate"}

func fetchBeisenInventoryPage(ctx context.Context, client *http.Client, b api.BeisenBoard, endpoint string, pageIndex *int) (string, *api.Document, *GreenhouseResponse, error) {
	if !b.ResourceMatches(endpoint) {
		return "", nil, nil, queue.ErrConfiguration
	}
	var body []byte
	method := "GET"
	if pageIndex != nil {
		if endpoint != b.APIURL() || b.PortalID == "" || *pageIndex < 0 || *pageIndex >= 50 {
			return "", nil, nil, queue.ErrConfiguration
		}
		method = "POST"
		body, _ = json.Marshal(struct {
			PageIndex     int
			PageSize      int
			KeyWords      string
			SpecialType   int
			PortalId      string
			DisplayFields []string
		}{*pageIndex, 1000, "", 0, b.PortalID, beisenDisplayFields})
	}
	var observation *GreenhouseResponse
	var failure error
	for attempt := 0; attempt < 3; attempt++ {
		requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		r, err := http.NewRequestWithContext(requestCtx, method, endpoint, bytes.NewReader(body))
		if err != nil {
			cancel()
			return "", nil, observation, queue.ErrConfiguration
		}
		r.Header.Set("User-Agent", ordinaryUserAgent)
		r.Header.Set("Accept", ordinaryAccept)
		if pageIndex != nil {
			r.Header.Set("Content-Type", "application/json")
		}
		resp, err := client.Do(r)
		failure = &DiscoveryError{Kind: "request_failed"}
		if err == nil {
			if resp.Request == nil || resp.Request.URL == nil {
				resp.Body.Close()
				cancel()
				return "", nil, observation, queue.ErrConfiguration
			}
			observation = &GreenhouseResponse{endpoint: endpoint, finalURL: resp.Request.URL.String(), status: resp.StatusCode}
			failure = &DiscoveryError{Kind: "http_status", Status: resp.StatusCode}
			if resp.StatusCode == 200 {
				reservation, policyURL := greenhouseHeaders(resp.Header)
				policyValue := ""
				if policyURL != nil {
					policyValue = *policyURL
				}
				signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policyValue}
				policyErr := policy.Check(signals, "", observation.finalURL)
				var raw []byte
				if policyErr == nil {
					raw, err = io.ReadAll(io.LimitReader(resp.Body, (64<<20)+1))
					observation.bytes = len(raw)
					if len(raw) > 64<<20 {
						resp.Body.Close()
						cancel()
						return "", nil, observation, &DiscoveryError{Kind: "body_limit"}
					}
				}
				source := jsonld.DecodeDocument(raw, resp.Header.Get("Content-Type"))
				if err == nil && policyErr == nil {
					policyErr = policy.Check(signals, source, observation.finalURL)
				}
				if policyErr != nil {
					var reserved *policy.Reservation
					if errors.As(policyErr, &reserved) {
						observation.reserved = true
						observation.policy = reserved.PolicyURL
						observation.reservationSource = reserved.Source
					}
					resp.Body.Close()
					cancel()
					return "", nil, observation, policyErr
				}
				if err == nil && source != "" {
					if pageIndex != nil {
						d, e := api.Decode(raw)
						if e == nil {
							if _, ok := d.Value.(map[string]any); ok {
								resp.Body.Close()
								cancel()
								return "", d, observation, nil
							}
						}
						failure = &DiscoveryError{Kind: "invalid_inventory"}
					} else {
						runes := []rune(source)
						if len(runes) > 2_000_001 {
							source = string(runes[:2_000_001])
						}
						classification, e := dom.ClassifyRendered(source, map[string]any{}, observation.finalURL)
						resp.Body.Close()
						cancel()
						if e != nil || classification["classification"] == "challenge" {
							return "", nil, observation, &DiscoveryError{Kind: "bot_challenge"}
						}
						return source, nil, observation, nil
					}
				} else {
					failure = &DiscoveryError{Kind: "body_failed"}
				}
			} else if resp.StatusCode != 202 && resp.StatusCode != 401 && resp.StatusCode != 403 && resp.StatusCode != 408 && resp.StatusCode != 425 && resp.StatusCode != 429 && resp.StatusCode < 500 {
				resp.Body.Close()
				cancel()
				return "", nil, observation, failure
			}
			resp.Body.Close()
		}
		cancel()
		if ctx.Err() != nil {
			return "", nil, observation, ctx.Err()
		}
		if attempt < 2 {
			delay := time.Duration(float64(500*time.Millisecond) * float64(int64(1)<<attempt) * (0.5 + rand.Float64()))
			if err := pauseRich(ctx, delay); err != nil {
				return "", nil, observation, err
			}
		}
	}
	return "", nil, observation, failure
}

func beisenRichJobs(jobs []api.Job, hybrid bool) ([]RichMonitorJob, error) {
	result := make([]RichMonitorJob, 0, len(jobs))
	for _, j := range jobs {
		job := RichMonitorJob{URL: j.URL, Locations: j.Locations, EmploymentType: j.EmploymentType, DatePosted: j.DatePosted, Metadata: j.Metadata, Hybrid: hybrid}
		if s, ok := j.Title.(string); ok {
			job.Title = &s
		}
		if s, ok := j.Description.(string); ok {
			body, err := enrichment.NormalizeDescriptionHTML(s)
			if err != nil {
				return nil, err
			}
			job.Description = body
		}
		result = append(result, job)
	}
	return result, nil
}

// Bootstrap, page completeness and identity checks finish before any canonical
// write. A failed later page cannot publish the previously collected prefix.
func discoverBeisenInventory(ctx context.Context, verified *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	b, err := queue.BeisenMonitorOptions(config)
	if err != nil || verified == nil || p.Provider != "beisen" || p.Endpoint != b.RootURL() {
		return result, queue.ErrConfiguration
	}
	client := *verified
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	client.Jar, _ = cookiejar.New(nil)
	root, _, response, err := fetchBeisenInventoryPage(ctx, &client, b, b.RootURL(), nil)
	result.Response = response
	if err != nil {
		return result, err
	}
	if len([]rune(root)) > 2_000_000 {
		return result, &DiscoveryError{Kind: "body_limit"}
	}
	b, disabled, err := api.ResolveBeisenBoard(root, config["board_url"], b)
	if err != nil {
		return result, &DiscoveryError{Kind: "invalid_inventory", cause: err}
	}
	if disabled {
		result.Response.providerDisabled = true
		return result, &DiscoveryError{Kind: "provider_gone", Status: 200}
	}
	order := []string{}
	jobs := map[string]api.Job{}
	merge := func(rows []api.Job, firstWins bool) {
		for _, j := range rows {
			if _, seen := jobs[j.URL]; seen {
				result.Truncated = true
				if firstWins {
					continue
				}
			} else {
				order = append(order, j.URL)
			}
			jobs[j.URL] = j
		}
	}
	if b.Variant == "modern" {
		index := 0
		_, d, response, err := fetchBeisenInventoryPage(ctx, &client, b, b.APIURL(), &index)
		result.Response = response
		if err != nil {
			return result, err
		}
		rows, total, err := api.BeisenModernJobs(d, b)
		if err != nil {
			return result, err
		}
		pages := total / 1000
		if total%1000 != 0 {
			pages++
		}
		pages = max(1, pages)
		result.Truncated = total > 50000 || pages > 50
		mergePage := func(index int, d *api.Document, rows []api.Job, count int) error {
			data, _ := d.Value.(map[string]any)["Data"].([]any)
			if count != total || len(data) > 1000 || len(data) != min(1000, max(total-index*1000, 0)) || len(rows) != len(data) {
				result.Truncated = true
			}
			merge(rows, true)
			return nil
		}
		_ = mergePage(0, d, rows, total)
		for index = 1; index < min(pages, 50); index++ {
			_, d, response, err = fetchBeisenInventoryPage(ctx, &client, b, b.APIURL(), &index)
			result.Response = response
			if err != nil {
				return result, err
			}
			rows, count, err := api.BeisenModernJobs(d, b)
			if err != nil {
				return result, err
			}
			_ = mergePage(index, d, rows, count)
		}
		if len(jobs) != min(total, 50000) {
			result.Truncated = true
		}
		if result.Truncated && len(jobs) == 0 && total > 0 {
			return result, &DiscoveryError{Kind: "invalid_inventory"}
		}
	} else {
		page, _, response, err := fetchBeisenInventoryPage(ctx, &client, b, b.ListingURL(), nil)
		result.Response = response
		if err != nil {
			return result, err
		}
		parse := func(page string) (api.BeisenLegacyPage, bool, error) {
			runes := []rune(page)
			limited := len(runes) > 2_000_000
			if limited {
				page = string(runes[:2_000_000])
			}
			parsed, err := api.ParseBeisenLegacyPage(page, b)
			return parsed, limited, err
		}
		first, limited, err := parse(page)
		if err != nil {
			return result, err
		}
		result.Truncated = limited || first.Pages > 50000 || first.Total != nil && *first.Total > 50000
		merge(first.Jobs, false)
		for index := 2; index <= min(first.Pages, 50000); index++ {
			page, _, response, err = fetchBeisenInventoryPage(ctx, &client, b, b.ListingURL()+"?PageIndex="+strconv.Itoa(index), nil)
			result.Response = response
			if err != nil {
				return result, err
			}
			parsed, limited, err := parse(page)
			if err != nil || len(parsed.Jobs) == 0 {
				return result, &DiscoveryError{Kind: "invalid_inventory"}
			}
			if limited || parsed.Pages != first.Pages || (parsed.Total == nil) != (first.Total == nil) || parsed.Total != nil && *parsed.Total != *first.Total {
				result.Truncated = true
			}
			merge(parsed.Jobs, false)
			if len(jobs) >= 50000 {
				result.Truncated = true
				break
			}
		}
		if len(jobs) == 0 && first.Pages > 1 {
			return result, &DiscoveryError{Kind: "invalid_inventory"}
		}
		if first.Total != nil && len(jobs) != min(*first.Total, 50000) {
			result.Truncated = true
		}
	}
	ordered := make([]api.Job, 0, len(order))
	for _, u := range order {
		ordered = append(ordered, jobs[u])
	}
	result.Jobs, err = beisenRichJobs(ordered, b.Variant == "legacy")
	return result, err
}
