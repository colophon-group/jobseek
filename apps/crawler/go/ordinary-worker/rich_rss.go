package worker

import (
	"context"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	teamtailor "github.com/colophon-group/jobseek/apps/crawler/go/teamtailor-rss-monitor"
)

func richResponseMatches(profile queue.GreenhouseMonitorProfile, endpoint string) bool {
	if profile.Provider == "smartrecruiters" || profile.Provider == "workable" {
		return queue.APIMonitorResourceMatches(profile, endpoint)
	}
	if endpoint == profile.Endpoint {
		return true
	}
	if profile.Provider == "lever" {
		return strings.HasPrefix(endpoint, strings.TrimSuffix(profile.Endpoint, "skip=0")+"skip=")
	}
	if profile.Profile == "personio.xml-skip/v1" {
		return personioResponseMatches(profile, endpoint)
	}
	if profile.Profile != "rss.teamtailor-skip/v1" {
		return false
	}
	u, err := url.Parse(endpoint)
	if err != nil {
		return false
	}
	q := u.Query()
	u.RawQuery = ""
	offset, err := strconv.Atoi(q.Get("offset"))
	return err == nil && u.String() == profile.Endpoint && len(q) == 2 && len(q["offset"]) == 1 && len(q["per_page"]) == 1 && q.Get("per_page") == "100" && offset >= 0 && offset < 100_000 && offset%100 == 0 && strconv.Itoa(offset) == q.Get("offset")
}

// Keep response provenance inside the sealed-client discovery path. The feed
// parser receives this same client; it cannot create a transport or subprocess.
type rssRichClient struct {
	client    *http.Client
	profile   queue.GreenhouseMonitorProfile
	response  *GreenhouseResponse
	responses []*GreenhouseResponse
}

func (c *rssRichClient) Do(request *http.Request) (*http.Response, error) {
	if !richResponseMatches(c.profile, request.URL.String()) || c.client == nil {
		return nil, &DiscoveryError{Kind: "invalid_configuration"}
	}
	response, err := c.client.Do(request)
	c.response = nil
	if err != nil {
		return nil, err
	}
	if response.Request == nil || response.Request.URL == nil {
		response.Body.Close()
		return nil, &DiscoveryError{Kind: "invalid_response"}
	}
	reservation, policy := greenhouseHeaders(response.Header)
	c.response = &GreenhouseResponse{endpoint: request.URL.String(), finalURL: response.Request.URL.String(), status: response.StatusCode, reserved: reservation == "1", policy: policy}
	if c.profile.Provider == "personio" {
		c.responses = append(c.responses, c.response)
	}
	// The parser's header lookup must see the same joined values as httpx;
	// duplicate reservation flags must not turn into a single literal 1.
	copy := *response
	copy.Header = response.Header.Clone()
	if reservation != "1" {
		reservation = ""
	}
	copy.Header.Set("TDM-Reservation", reservation)
	return &copy, nil
}

func discoverTeamtailorRich(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile) (RichDiscovery, error) {
	observed := &rssRichClient{client: client, profile: profile}
	found, err := teamtailor.Fetch(ctx, observed, profile.Endpoint)
	if ctx.Err() != nil {
		return RichDiscovery{}, ctx.Err()
	}
	if err != nil {
		// A failed later page never supplies a partial successful inventory or
		// provider-gone signal. Publisher policy still binds its actual page.
		if observed.response != nil && observed.response.reserved {
			return RichDiscovery{Response: observed.response}, &DiscoveryError{Kind: "publisher_reserved", Status: observed.response.status}
		}
		return RichDiscovery{}, &DiscoveryError{Kind: "feed_failed"}
	}
	result := RichDiscovery{Jobs: []RichMonitorJob{}, Truncated: found.Truncated, Response: observed.response}
	for _, job := range found.Jobs {
		var locationType any
		if job.JobLocationType != nil {
			locationType = *job.JobLocationType
		}
		result.Jobs = append(result.Jobs, RichMonitorJob{URL: job.URL, Title: job.Title, Description: job.Description, Locations: job.Locations, DatePosted: job.DatePosted, Metadata: job.Metadata, JobLocationType: locationType})
	}
	return result, nil
}
