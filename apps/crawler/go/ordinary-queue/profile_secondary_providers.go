package queue

import (
	"encoding/json"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func SecondaryProvider(provider string) bool {
	return provider == "softgarden" || provider == "ukg" || provider == "bamboohr" || provider == "recruiter_co_kr"
}
func secondaryMonitorEnrichment(config map[string]string) ([]string, error) {
	allowed := map[string]bool{"description": true}
	if config["crawler_type"] == "bamboohr" {
		allowed = map[string]bool{"description": true, "locations": true, "employment_type": true, "job_location_type": true, "date_posted": true}
	}
	if config["crawler_type"] == "recruiter_co_kr" {
		allowed = map[string]bool{}
	}
	return monitorEnrichmentFields(config, allowed)
}
func inspectSecondaryMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if config["monitor_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := secondaryMonitorEnrichment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	provider := config["crawler_type"]
	var profile, endpoint string
	switch provider {
	case "softgarden":
		o, e := api.SoftgardenOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "softgarden.inline-urls/v1", o.ListingURL()
	case "ukg":
		o, e := api.UKGOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "ukg.search-items/v1", o.SearchURL()
	case "bamboohr":
		o, e := api.BambooHROptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "bamboohr.careers-list/v1", o.ListingURL()
	case "recruiter_co_kr":
		if e := feedRichDetailAssignment(config); e != nil {
			return GreenhouseMonitorProfile{}, e
		}
		o, e := api.RecruiterKROptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "recruiter-co-kr.jobflex/v1", o.ListURL()
	default:
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, provider, profile, provider, endpoint)
}
func SecondaryMonitorResourceMatches(p GreenhouseMonitorProfile, config map[string]string, resource string) bool {
	if config["crawler_type"] != p.Provider {
		return false
	}
	switch p.Provider {
	case "softgarden":
		o, e := api.SoftgardenOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "softgarden.inline-urls/v1" && p.Endpoint == o.ListingURL() && o.ResourceMatches(resource)
	case "ukg":
		o, e := api.UKGOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "ukg.search-items/v1" && p.Endpoint == o.SearchURL() && o.ResourceMatches(resource)
	case "bamboohr":
		o, e := api.BambooHROptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "bamboohr.careers-list/v1" && p.Endpoint == o.ListingURL() && o.ResourceMatches(resource)
	case "recruiter_co_kr":
		o, e := api.RecruiterKROptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "recruiter-co-kr.jobflex/v1" && p.Endpoint == o.ListURL() && o.ResourceMatches(resource)
	}
	return false
}
func SecondaryMonitorGone(config map[string]string, resource string, status int, disabled bool) bool {
	switch config["crawler_type"] {
	case "softgarden":
		o, e := api.SoftgardenOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && resource == o.ListingURL() && status == 404 && !disabled
	case "ukg":
		o, e := api.UKGOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && resource == o.SearchURL() && (status == 404 || status == 410) && !disabled
	case "bamboohr":
		o, e := api.BambooHROptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && resource == o.ListingURL() && ((status == 404 || status == 410) && !disabled || disabled && (status == 301 || status == 302 || status == 303 || status == 307 || status == 308))
	case "recruiter_co_kr":
		o, e := api.RecruiterKROptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && resource == o.ListURL() && status == 400 && disabled
	}
	return false
}
