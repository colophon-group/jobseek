package worker

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/cookiejar"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	smartrecruiters "github.com/colophon-group/jobseek/apps/crawler/go/smartrecruiters-monitor"
	workable "github.com/colophon-group/jobseek/apps/crawler/go/workable-monitor"
)

type apiInventoryClient struct {
	client   *http.Client
	profile  queue.GreenhouseMonitorProfile
	response *GreenhouseResponse
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
	c.response = nil
	if err != nil {
		return nil, err
	}
	if r.Request == nil || r.Request.URL == nil {
		r.Body.Close()
		return nil, &DiscoveryError{Kind: "invalid_response"}
	}
	c.response = &GreenhouseResponse{endpoint: request.URL.String(), finalURL: r.Request.URL.String(), status: r.StatusCode}
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
		if len(found.Jobs) > 0 {
			return result, queue.ErrUnsupportedProfile
		}
		var reserved *smartrecruiters.Failure
		if errors.As(err, &reserved) && reserved.Kind == "tdm" && observed.response != nil {
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
