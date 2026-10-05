package worker

import (
	"context"
	"errors"
	"html"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

var nextdataTitle = regexp.MustCompile(`(?is)<title(?:\s[^>]*)?>(.*?)</title>`)

type nextdataPage struct {
	document *apisniffer.Document
	items    []any
	response *GreenhouseResponse
	err      error
}

func fetchNextdataPage(ctx context.Context, client *http.Client, o apisniffer.NextdataOptions, endpoint string, required, allowEmpty bool) nextdataPage {
	result := nextdataPage{}
	if !o.ResourceMatches(endpoint) {
		result.err = queue.ErrConfiguration
		return result
	}
	attempts := 1
	if required {
		attempts = 3
	}
	for attempt := 0; attempt < attempts; attempt++ {
		result = nextdataPage{}
		requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		r, err := http.NewRequestWithContext(requestCtx, http.MethodGet, endpoint, nil)
		if err != nil {
			cancel()
			result.err = err
			return result
		}
		r.Header.Set("User-Agent", ordinaryUserAgent)
		r.Header.Set("Accept", ordinaryAccept)
		for k, v := range o.Headers {
			r.Header.Set(k, v)
		}
		resp, err := client.Do(r)
		result.err = &DiscoveryError{Kind: "request_failed"}
		if err == nil {
			if resp.Request == nil || resp.Request.URL == nil {
				resp.Body.Close()
				cancel()
				result.err = queue.ErrConfiguration
				return result
			}
			result.response = &GreenhouseResponse{endpoint: endpoint, finalURL: resp.Request.URL.String(), status: resp.StatusCode}
			if resp.StatusCode == 200 {
				reservation, policyURL := greenhouseHeaders(resp.Header)
				p := ""
				if policyURL != nil {
					p = *policyURL
				}
				signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &p}
				policyErr := policy.Check(signals, "", result.response.finalURL)
				if policyErr == nil {
					body, readErr := io.ReadAll(io.LimitReader(resp.Body, (64<<20)+1))
					result.response.bytes = len(body)
					if len(body) > 64<<20 {
						resp.Body.Close()
						cancel()
						result.err = &DiscoveryError{Kind: "body_limit"}
						return result
					}
					if readErr == nil {
						source := jsonld.DecodeDocument(body, resp.Header.Get("Content-Type"))
						count := 0
						for offset := range source {
							if count == 4_000_000 {
								source = source[:offset]
								break
							}
							count++
						}
						policyErr = policy.Check(signals, source, result.response.finalURL)
						if policyErr == nil {
							result.err = &DiscoveryError{Kind: "invalid_inventory"}
							if o.ExpectedTitle != "" {
								m := nextdataTitle.FindStringSubmatch(source)
								if len(m) != 2 || strings.Join(strings.Fields(html.UnescapeString(m[1])), " ") != o.ExpectedTitle {
									resp.Body.Close()
									cancel()
									result.err = &DiscoveryError{Kind: "tenant_mismatch"}
									return result
								}
							}
							document, parseErr := apisniffer.ParseEmbeddedMonitorDocument(source, o.Source)
							if parseErr == nil {
								items, parseErr := document.NextdataPageItems(o)
								if parseErr == nil && (!required || len(items) > 0 || allowEmpty) {
									result.document, result.items, result.err = document, items, nil
								}
							}
						}
					} else {
						result.err = &DiscoveryError{Kind: "body_failed"}
					}
				}
				if policyErr != nil {
					var reserved *policy.Reservation
					if errors.As(policyErr, &reserved) {
						result.response.reserved = true
						result.response.policy = reserved.PolicyURL
						result.response.reservationSource = reserved.Source
					}
					resp.Body.Close()
					cancel()
					result.err = policyErr
					return result
				}
			} else {
				result.err = &DiscoveryError{Kind: "http_status", Status: resp.StatusCode}
			}
			resp.Body.Close()
		}
		cancel()
		if ctx.Err() != nil {
			result.err = ctx.Err()
			return result
		}
		if result.err == nil {
			return result
		}
		if attempt+1 < attempts {
			if err := pauseRich(ctx, 500*time.Millisecond*time.Duration(1<<attempt)); err != nil {
				result.err = err
				return result
			}
		}
	}
	return result
}

// Each callback is a verified streamed batch: first page, then groups of ten
// pages. Results retain provider page order while requests use its bounded
// concurrency. Failed later groups cannot delete unseen jobs or erase earlier
// committed chunks. No callback grants database authority by itself.
func discoverNextdataInventory(ctx context.Context, verified *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, yield func([]RichMonitorJob) error) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := queue.NextdataMonitorOptions(config)
	if err != nil || verified == nil || p.Provider != "nextdata" || p.Endpoint != o.BoardURL {
		return result, queue.ErrConfiguration
	}
	client := *verified
	client.Jar, _ = cookiejar.New(nil)
	if len(o.Headers) > 0 {
		client.Jar = nil
		base, _ := url.Parse(o.BoardURL)
		original := client.CheckRedirect
		client.CheckRedirect = func(r *http.Request, via []*http.Request) error {
			if len(via) > 5 || r.URL.Scheme != base.Scheme || r.URL.Host != base.Host {
				return queue.ErrConfiguration
			}
			if original != nil {
				return original(r, via)
			}
			return nil
		}
	}
	first := fetchNextdataPage(ctx, &client, o, p.Endpoint, o.Pagination != nil, true)
	result.Response = first.response
	if first.err != nil {
		var failure *DiscoveryError
		mismatch := errors.As(first.err, &failure) && failure.Kind == "tenant_mismatch"
		if ctx.Err() != nil || first.response != nil && first.response.reserved || o.Strict || o.Pagination != nil || mismatch {
			return result, first.err
		}
		// Lenient non-paginated legacy fetch/parser failures yield no inventory;
		// its existing empty/drop guard still owns the terminal decision.
		return result, nil
	}
	pages, total, err := first.document.NextdataPageCount(o)
	if err != nil {
		return result, err
	}
	if len(first.items) == 0 && o.Pagination != nil && !(total != nil && *total == 0 || o.Pagination.TotalRecords == "" && pages <= 1) {
		return result, apisniffer.ErrInventory
	}
	seen := map[string]bool{}
	project := func(page nextdataPage) ([]RichMonitorJob, error) {
		items, err := o.FilterNextdataItems(page.items)
		if err != nil {
			return nil, err
		}
		jobs, err := page.document.ProjectNextdataItems(items, o.Template, o.SlugFields, o.Fields)
		if err != nil {
			return nil, err
		}
		out := []RichMonitorJob{}
		allow, err := dom.CompileURLPattern(o.URLAllowlist)
		if err != nil {
			return nil, err
		}
		for _, j := range jobs {
			if len(o.Fields) > 0 && o.URLAllowlist != "" {
				m, err := allow.FindStringMatch(j.URL)
				if err != nil || m == nil {
					return nil, apisniffer.ErrInventory
				}
				at, n := m.ByteRange()
				if at != 0 || n != len(j.URL) {
					return nil, apisniffer.ErrInventory
				}
			}
			title, err := executor.CoerceText(j.Title)
			if err != nil {
				return nil, err
			}
			description, err := executor.CoerceText(j.Description)
			if err != nil {
				return nil, err
			}
			out = append(out, RichMonitorJob{URL: j.URL, Title: title, Description: description, Locations: j.Locations, DatePosted: j.DatePosted, Metadata: j.Metadata, EmploymentType: j.EmploymentType, JobLocationType: j.JobLocationType})
		}
		return out, nil
	}
	emit := func(jobs []RichMonitorJob) error {
		for _, j := range jobs {
			seen[j.URL] = true
		}
		result.Jobs = append(result.Jobs, jobs...)
		if yield != nil {
			return yield(jobs)
		}
		return nil
	}
	jobs, err := project(first)
	if err != nil {
		return result, err
	}
	if err := emit(jobs); err != nil {
		return result, err
	}
	if pages > 50_001 {
		return result, &DiscoveryError{Kind: "inventory_limit"}
	}
	if pages > 1 && o.Pagination != nil {
		for start := 1; start < pages; start += 10 {
			end := min(start+10, pages)
			batch := make([]nextdataPage, end-start)
			sem := make(chan struct{}, o.Pagination.Concurrency)
			var wg sync.WaitGroup
			for index := start; index < end; index++ {
				wg.Add(1)
				go func(index int) {
					defer wg.Done()
					select {
					case sem <- struct{}{}:
						defer func() { <-sem }()
					case <-ctx.Done():
						batch[index-start].err = ctx.Err()
						return
					}
					endpoint, err := o.PageURL(index)
					if err != nil {
						batch[index-start].err = err
						return
					}
					batch[index-start] = fetchNextdataPage(ctx, &client, o, endpoint, true, false)
				}(index)
			}
			wg.Wait()
			// A publisher witness on any concurrent page remains authoritative,
			// including when another page in the group failed.
			for _, page := range batch {
				if page.response != nil && page.response.reserved {
					result.Response = page.response
					return result, page.err
				}
			}
			for _, page := range batch {
				if page.err != nil {
					result.Response = page.response
					return result, page.err
				}
			}
			chunk := []RichMonitorJob{}
			for _, page := range batch {
				result.Response = page.response
				jobs, err := project(page)
				if err != nil {
					return result, err
				}
				chunk = append(chunk, jobs...)
			}
			if len(chunk) > 0 {
				if err := emit(chunk); err != nil {
					return result, err
				}
			}
		}
	}
	if total != nil && *total <= 50_000 && len(seen) != *total {
		return result, &DiscoveryError{Kind: "incomplete_inventory"}
	}
	return result, ctx.Err()
}
