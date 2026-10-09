package worker

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/cookiejar"
	"sort"
	"sync"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	smartrecruiters "github.com/colophon-group/jobseek/apps/crawler/go/smartrecruiters-monitor"
	workable "github.com/colophon-group/jobseek/apps/crawler/go/workable-monitor"
)

type apiInventoryClient struct {
	client    *http.Client
	profile   queue.GreenhouseMonitorProfile
	response  *GreenhouseResponse
	mu        sync.Mutex
	responses map[string]*GreenhouseResponse
}

func (c *apiInventoryClient) Do(request *http.Request) (*http.Response, error) {
	if c.client == nil || !queue.APIMonitorResourceMatches(c.profile, request.URL.String()) {
		return nil, &DiscoveryError{Kind: "invalid_configuration"}
	}
	method := "GET"
	if c.profile.Provider == "workable" && request.URL.String() == c.profile.Endpoint {
		method = "POST"
	}
	if request.Method != method {
		return nil, &DiscoveryError{Kind: "invalid_configuration"}
	}
	r, err := c.client.Do(request)
	if err != nil {
		return nil, err
	}
	if r.Request == nil || r.Request.URL == nil {
		r.Body.Close()
		return nil, &DiscoveryError{Kind: "invalid_response"}
	}
	if !queue.APIMonitorResourceMatches(c.profile, r.Request.URL.String()) {
		r.Body.Close()
		return nil, &DiscoveryError{Kind: "invalid_response"}
	}
	c.mu.Lock()
	c.response = &GreenhouseResponse{endpoint: request.URL.String(), finalURL: r.Request.URL.String(), status: r.StatusCode}
	if c.responses == nil {
		c.responses = map[string]*GreenhouseResponse{}
	}
	c.responses[request.URL.String()] = c.response
	c.mu.Unlock()
	return r, nil
}

func discoverAPIInventory(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
	if client == nil {
		return RichDiscovery{}, queue.ErrConfiguration
	}
	operationClient := *client
	jar, jarErr := cookiejar.New(nil)
	if jarErr != nil {
		return RichDiscovery{}, jarErr
	}
	operationClient.Jar = jar
	operationClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	observed := &apiInventoryClient{client: &operationClient, profile: profile}
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	var urls []string
	var err error
	if profile.Provider == "smartrecruiters" {
		var metadata smartrecruiters.Object
		if json.Unmarshal([]byte(config["metadata"]), &metadata) != nil {
			return result, queue.ErrConfiguration
		}
		found, failure := smartrecruiters.FetchWithClient(ctx, config["board_url"], metadata, observed)
		urls, result.Truncated, err = found.URLs, found.Truncated, failure
		if profile.Profile == "smartrecruiters.canonical-items/v1" && failure == nil {
			options, optionErr := smartrecruiters.OptionsFromMetadata(config["board_url"], metadata)
			if optionErr != nil {
				return result, queue.ErrConfiguration
			}
			urls = nil
			for _, fields := range found.Jobs {
				job, projectErr := smartCanonicalRichJob(options, fields)
				if projectErr != nil {
					return RichDiscovery{Response: observed.response}, projectErr
				}
				result.Jobs = append(result.Jobs, job)
			}
		} else if len(found.Jobs) > 0 {
			return result, queue.ErrUnsupportedProfile
		}
		var reserved *smartrecruiters.Failure
		if errors.As(err, &reserved) && reserved.Kind == "tdm" {
			observed.response = observed.responses[reserved.URL]
			if observed.response == nil {
				return result, queue.ErrConfiguration
			}
			observed.response.reserved = true
			if reserved.Policy != "" {
				observed.response.policy = &reserved.Policy
			}
			observed.response.reservationSource = reserved.Source
		}
	} else {
		found, failure := workable.Fetch(ctx, observed, profile.Token, nil)
		urls, result.Truncated, err = found.URLs, found.Truncated, failure
		if found.ErrorKind == "tdm" && observed.response != nil {
			observed.response.reserved = true
			if found.TDMPolicy != "" {
				observed.response.policy = &found.TDMPolicy
			}
			observed.response.reservationSource = found.TDMSource
		}
	}
	result.Response = observed.response
	if ctx.Err() != nil {
		return result, ctx.Err()
	}
	if err != nil {
		return result, &DiscoveryError{Kind: "inventory_failed", cause: err}
	}
	for _, raw := range urls {
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: raw})
	}
	return result, nil
}

// Project the existing provider SDK's complete rich output without discarding
// localized titles or its explicit durable identity. Shared normalization and
// content preparation remain responsible for canonical fields and descriptions.
func smartCanonicalRichJob(options smartrecruiters.Options, value smartrecruiters.Job) (RichMonitorJob, error) {
	fields := map[string]any{"url": value.URL, "title": value.Title, "description": value.Description, "locations": value.Locations, "employment_type": value.EmploymentType, "job_location_type": value.JobLocationType, "date_posted": value.DatePosted, "language": value.Language, "metadata": value.Metadata, "extras": value.Extras}
	if locations, ok := value.Locations.([]any); ok {
		converted := []string{}
		for _, raw := range locations {
			text, ok := raw.(string)
			if !ok {
				return RichMonitorJob{}, queue.ErrConfiguration
			}
			converted = append(converted, text)
		}
		fields["locations"] = converted
	}
	job, err := secondaryRichJob(fields)
	if err != nil {
		return job, err
	}
	job.SourceIdentity, _ = value.SourceIdentity.(string)
	if !options.IdentityMatches(job.URL, job.SourceIdentity) {
		return job, queue.ErrConfiguration
	}
	if locales, ok := value.Localizations.(map[string]any); ok {
		remaining := []string{}
		for locale := range locales {
			remaining = append(remaining, locale)
		}
		sort.Strings(remaining)
		primary, _ := value.Language.(string)
		seen := map[string]bool{}
		order := append([]string{primary}, options.Languages...)
		if options.Identity == "job-location-v1" {
			order = []string{primary}
		}
		for _, locale := range append(order, remaining...) {
			content, ok := locales[locale].(map[string]any)
			if !ok || seen[locale] {
				continue
			}
			seen[locale] = true
			job.LocalizationLocales = append(job.LocalizationLocales, locale)
			if title, ok := content["title"].(string); ok && title != "" {
				job.LocalizedTitles = append(job.LocalizedTitles, title)
			}
		}
	}
	return job, nil
}
