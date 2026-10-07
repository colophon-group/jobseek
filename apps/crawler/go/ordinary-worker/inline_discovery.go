package worker

import (
	"context"
	"math/rand/v2"
	"net/http"
	"net/http/cookiejar"
	"strings"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func fetchInlineCandidate(ctx context.Context, client *http.Client, o api.InlineMonitorOptions, c api.InlineFetchCandidate) (string, *GreenhouseResponse, error) {
	var observation *GreenhouseResponse
	for attempt := 0; attempt < o.Attempts; attempt++ {
		pageCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		doc, err := jsonld.FetchDocumentWithClient(pageCtx, c.URL, c.Document, client)
		cancel()
		observation = nil
		if doc.Responses > 0 {
			var policyURL *string
			if doc.TDMPolicy != "" {
				s := doc.TDMPolicy
				policyURL = &s
			}
			observation = &GreenhouseResponse{endpoint: c.URL, finalURL: doc.FinalURL, status: doc.Status, reserved: doc.ErrorKind == "tdm", policy: policyURL, reservationSource: doc.TDMSource, bytes: doc.Bytes}
		}
		if doc.ErrorKind == "tdm" {
			return "", observation, err
		}
		if ctx.Err() != nil {
			return "", observation, ctx.Err()
		}
		if err == nil && doc.Status == 200 && len(doc.Body) > 0 {
			return jsonld.DecodeDocument(doc.Body, doc.ContentType), observation, nil
		}
		retry := err != nil || doc.Status == 200 || doc.Status == 408 || doc.Status == 425 || doc.Status == 429 || doc.Status >= 500 && doc.Status < 600 || o.Transient403 && (doc.Status == 401 || doc.Status == 403)
		if !retry || attempt+1 >= o.Attempts {
			return "", observation, &DiscoveryError{Kind: "inventory_failed", Status: doc.Status}
		}
		delay := time.Duration(float64(500*time.Millisecond) * float64(uint64(1)<<uint(attempt)) * (0.5 + rand.Float64()))
		if err = pauseRich(ctx, delay); err != nil {
			return "", observation, err
		}
	}
	return "", observation, &DiscoveryError{Kind: "inventory_failed"}
}

func discoverInlineInventory(ctx context.Context, verified *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	return discoverInlineInventoryAt(ctx, verified, p, config, time.Now())
}

func discoverInlineInventoryAt(ctx context.Context, verified *http.Client, p queue.GreenhouseMonitorProfile, config map[string]string, now time.Time) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	o, err := queue.InlineMonitorOptions(config)
	if err != nil || verified == nil || p.Provider != "inline" || p.Endpoint != o.BoardURL {
		return result, queue.ErrConfiguration
	}
	if len(o.Steps) == 0 {
		return result, nil
	}
	client := *verified
	client.Jar, err = cookiejar.New(nil)
	if err != nil {
		return result, err
	}
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	var source string
	var lastError error
	for _, candidate := range o.Candidates {
		source, result.Response, lastError = fetchInlineCandidate(ctx, &client, o, candidate)
		if ctx.Err() != nil || result.Response != nil && result.Response.reserved {
			return result, lastError
		}
		if lastError != nil {
			continue
		}
		if o.JSONPath != "" {
			d, err := api.Decode([]byte(source))
			if err != nil {
				lastError = err
				continue
			}
			value, err := api.Search(d.Value, o.JSONPath)
			if err != nil {
				lastError = err
				continue
			}
			s, ok := value.(string)
			if !ok || s == "" {
				lastError = api.ErrInventory
				continue
			}
			source = s
		}
		if o.Contains != "" && !strings.Contains(source, o.Contains) {
			lastError = api.ErrInventory
			continue
		}
		break
	}
	if lastError != nil {
		return result, lastError
	}
	return parseInlineInventoryDocument(ctx, result, o, source, now)
}

func parseInlineInventoryDocument(ctx context.Context, result RichDiscovery, o api.InlineMonitorOptions, source string, now time.Time) (RichDiscovery, error) {
	if o.Contains != "" && !strings.Contains(source, o.Contains) {
		return result, api.ErrInventory
	}
	classification, err := dom.ClassifyDocument(source, dom.Object{}, o.BoardURL)
	if err != nil {
		return result, err
	}
	if classification["classification"] == "challenge" {
		return result, executor.ErrBotChallenge
	}
	inventory, err := api.ParseInlineDocument(ctx, source, o.BoardURL, o.InlineOptions, now)
	if err != nil {
		return result, err
	}
	result.Truncated = inventory.Truncated
	result.VerifiedEmptyReason = inventory.VerifiedEmptyReason
	for _, j := range inventory.Jobs {
		if !o.PostingURLMatches(j.URL) {
			return RichDiscovery{Response: result.Response}, queue.ErrConfiguration
		}
		title, err := executor.CoerceText(j.Title)
		if err != nil {
			return RichDiscovery{Response: result.Response}, err
		}
		description, err := executor.CoerceText(j.Description)
		if err != nil {
			return RichDiscovery{Response: result.Response}, err
		}
		if description != nil {
			normalized, err := enrichment.NormalizeDescriptionHTML(*description)
			if err != nil {
				return RichDiscovery{Response: result.Response}, err
			}
			description = normalized
		}
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: j.URL, Title: title, Description: description, Locations: j.Locations, EmploymentType: j.EmploymentType, JobLocationType: j.JobLocationType, DatePosted: j.DatePosted, Extras: j.Extras})
	}
	return result, nil
}
