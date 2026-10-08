package queue

import (
	"encoding/json"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func SecondaryProvider(provider string) bool {
	if provider == "umantis" {
		return true
	}
	if TenthProvider(provider) {
		return true
	}
	if provider == "talentbrew" || provider == "beehire" || provider == "hirehive" || provider == "welcometothejungle" || provider == "computrabajo" || provider == "ycombinator" || provider == "earcu" || provider == "cvwarehouse" || provider == "woowa" || provider == "deel" || provider == "hibob" || provider == "traffit" || provider == "manatal" || provider == "hrmos" || provider == "recruiterbox" || provider == "jobs_ch" {
		return true
	}
	return provider == "dayforce" || provider == "adp" || provider == "cornerstone" || provider == "paylocity" || provider == "paycom" || provider == "rippling" || provider == "comeet" || provider == "jobvite" || provider == "softgarden" || provider == "ukg" || provider == "bamboohr" || provider == "recruiter_co_kr"
}
func paycomEnrichmentFields(config map[string]string) ([]string, error) {
	return monitorEnrichmentFields(config, map[string]bool{"title": true, "description": true, "locations": true, "employment_type": true, "job_location_type": true, "date_posted": true, "base_salary": true})
}
func secondaryMonitorEnrichment(config map[string]string) ([]string, error) {
	if config["crawler_type"] == "umantis" {
		return monitorEnrichmentFields(config, map[string]bool{"description": true, "locations": true, "title": true, "employment_type": true})
	}
	if TenthProvider(config["crawler_type"]) {
		allowed := map[string]bool{}
		if config["crawler_type"] == "typify" {
			allowed["description"] = true
		}
		return monitorEnrichmentFields(config, allowed)
	}
	if config["crawler_type"] == "talentbrew" || config["crawler_type"] == "beehire" || config["crawler_type"] == "hirehive" || config["crawler_type"] == "welcometothejungle" || config["crawler_type"] == "earcu" || config["crawler_type"] == "cvwarehouse" || config["crawler_type"] == "woowa" || config["crawler_type"] == "deel" || config["crawler_type"] == "hibob" || config["crawler_type"] == "traffit" || config["crawler_type"] == "manatal" || config["crawler_type"] == "hrmos" || config["crawler_type"] == "recruiterbox" || config["crawler_type"] == "jobs_ch" {
		return monitorEnrichmentFields(config, map[string]bool{})
	}
	allowed := map[string]bool{"description": true}
	if config["crawler_type"] == "adp" || config["crawler_type"] == "paylocity" {
		return monitorEnrichmentFields(config, map[string]bool{"title": true, "description": true, "locations": true, "employment_type": true, "job_location_type": true, "date_posted": true, "base_salary": true})
	}
	if config["crawler_type"] == "paycom" {
		return paycomEnrichmentFields(config)
	}
	if config["crawler_type"] == "bamboohr" {
		allowed = map[string]bool{"description": true, "locations": true, "employment_type": true, "job_location_type": true, "date_posted": true}
	}
	if config["crawler_type"] == "recruiter_co_kr" || config["crawler_type"] == "comeet" {
		allowed = map[string]bool{}
	}
	return monitorEnrichmentFields(config, allowed)
}
func inspectSecondaryMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if TenthProvider(config["crawler_type"]) {
		return inspectTenthMonitor(boardID, config, md)
	}
	if config["crawler_type"] == "dayforce" {
		return inspectDayforceMonitor(boardID, config, md)
	}
	if config["monitor_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := secondaryMonitorEnrichment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	provider := config["crawler_type"]
	var profile, endpoint string
	switch provider {
	case "umantis":
		o, e := api.UmantisOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "umantis.listing-urls/v1", o.Listing
	case "talentbrew":
		o, e := api.TalentBrewOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "talentbrew.listing-urls/v1", o.BoardURL
	case "beehire", "hirehive", "welcometothejungle", "computrabajo", "ycombinator":
		if provider != "computrabajo" && provider != "ycombinator" {
			if e := feedRichDetailAssignment(config); e != nil {
				return GreenhouseMonitorProfile{}, e
			}
		}
		o, e := api.NinthProviderOptionsFromMetadata(provider, config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = o.Profile(), o.ListingURL()
	case "earcu", "cvwarehouse", "woowa":
		if e := feedRichDetailAssignment(config); e != nil {
			return GreenhouseMonitorProfile{}, e
		}
		o, e := api.EighthProviderOptionsFromMetadata(provider, config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = o.Profile(), o.ListingURL()
	case "deel", "hibob", "traffit":
		if e := feedRichDetailAssignment(config); e != nil {
			return GreenhouseMonitorProfile{}, e
		}
		o, e := api.SeventhProviderOptionsFromMetadata(provider, config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = provider+".public-items/v1", o.ListingURL()
	case "recruiterbox":
		o, e := api.RecruiterboxOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "recruiterbox.listing-urls/v1", o.PageURL(1)
	case "jobs_ch":
		o, e := api.JobCloudOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "jobs_ch.company-urls/v1", o.SearchURL(1)
	case "manatal":
		if e := feedRichDetailAssignment(config); e != nil {
			return GreenhouseMonitorProfile{}, e
		}
		o, e := api.ManatalOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "manatal.career-items/v1", o.ListingURL(1)
	case "hrmos":
		o, e := api.HRMOSOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "hrmos.listing-urls/v1", o.ListingURL(1)
	case "adp":
		o, e := api.ADPOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "adp.search-items/v1", o.SearchURL(1)
	case "cornerstone":
		if e := feedRichDetailAssignment(config); e != nil {
			return GreenhouseMonitorProfile{}, e
		}
		o, e := api.CornerstoneOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "cornerstone.search-items/v1", o.ListingURL()
	case "paylocity":
		o, e := api.PaylocityOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = o.Profile(), o.Listing
	case "paycom":
		o, e := api.PaycomOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "paycom.preview-items/v1", o.PortalURL()
	case "rippling":
		o, e := api.RipplingOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "rippling.v1-urls/v1", o.ListingURL()

	case "comeet":
		if e := feedRichDetailAssignment(config); e != nil {
			return GreenhouseMonitorProfile{}, e
		}
		o, e := api.ComeetOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "comeet.hosted-items/v1", o.Endpoint
		if o.API {
			profile = "comeet.api-items/v1"
		}
	case "jobvite":
		o, e := api.JobviteOptionsFromMetadata(config["board_url"], config["metadata"])
		if e != nil {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		profile, endpoint = "jobvite.listing-urls/v1", o.Endpoint

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
	case "umantis":
		o, e := api.UmantisOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && (p.Profile == "umantis.listing-urls/v1" || p.Profile == "umantis.proxy-listing-urls/v1") && p.Endpoint == o.Listing && o.ResourceMatches(resource)
	case "intervieweb", "typify", "universia", "talentreef":
		o, e := api.TenthProviderOptionsFromMetadata(p.Provider, config["board_url"], config["metadata"])
		return e == nil && p.Profile == o.Profile() && p.Endpoint == o.ListingURL() && o.ResourceMatches(resource)
	case "talentbrew":
		o, e := api.TalentBrewOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "talentbrew.listing-urls/v1" && p.Endpoint == o.BoardURL && o.ResourceMatches(resource)
	case "beehire", "hirehive", "welcometothejungle", "computrabajo", "ycombinator":
		o, e := api.NinthProviderOptionsFromMetadata(p.Provider, config["board_url"], config["metadata"])
		return e == nil && p.Profile == o.Profile() && p.Endpoint == o.ListingURL() && o.ResourceMatches(resource)
	case "earcu", "cvwarehouse", "woowa":
		o, e := api.EighthProviderOptionsFromMetadata(p.Provider, config["board_url"], config["metadata"])
		return e == nil && p.Profile == o.Profile() && p.Endpoint == o.ListingURL() && o.ResourceMatches(resource)
	case "deel", "hibob", "traffit":
		o, e := api.SeventhProviderOptionsFromMetadata(p.Provider, config["board_url"], config["metadata"])
		return e == nil && p.Profile == p.Provider+".public-items/v1" && p.Endpoint == o.ListingURL() && o.ResourceMatches(resource)
	case "recruiterbox":
		o, e := api.RecruiterboxOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "recruiterbox.listing-urls/v1" && p.Endpoint == o.PageURL(1) && o.ResourceMatches(resource)
	case "jobs_ch":
		o, e := api.JobCloudOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "jobs_ch.company-urls/v1" && p.Endpoint == o.SearchURL(1) && o.ResourceMatches(resource)
	case "manatal":
		o, e := api.ManatalOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "manatal.career-items/v1" && p.Endpoint == o.ListingURL(1) && o.ResourceMatches(resource)
	case "hrmos":
		o, e := api.HRMOSOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "hrmos.listing-urls/v1" && p.Endpoint == o.ListingURL(1) && o.ResourceMatches(resource)
	case "dayforce":
		board, _, e := api.DayforceOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == dayforceMonitorProfile && p.Endpoint == board.ListingURL() && board.ResourceMatches(resource)
	case "adp":
		o, e := api.ADPOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "adp.search-items/v1" && p.Endpoint == o.SearchURL(1) && o.ResourceMatches(resource)
	case "cornerstone":
		o, e := api.CornerstoneOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "cornerstone.search-items/v1" && p.Endpoint == o.ListingURL() && o.ResourceMatches(resource)
	case "paylocity":
		o, e := api.PaylocityOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == o.Profile() && p.Endpoint == o.Listing && o.ResourceMatches(resource)
	case "paycom":
		o, e := api.PaycomOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "paycom.preview-items/v1" && p.Endpoint == o.PortalURL() && o.ResourceMatches(resource)
	case "rippling":
		o, e := api.RipplingOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "rippling.v1-urls/v1" && p.Endpoint == o.ListingURL() && o.ResourceMatches(resource)

	case "comeet":
		o, e := api.ComeetOptionsFromMetadata(config["board_url"], config["metadata"])
		profile := "comeet.hosted-items/v1"
		if o.API {
			profile = "comeet.api-items/v1"
		}
		return e == nil && p.Profile == profile && p.Endpoint == o.Endpoint && o.ResourceMatches(resource)
	case "jobvite":
		o, e := api.JobviteOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && p.Profile == "jobvite.listing-urls/v1" && p.Endpoint == o.Endpoint && o.ResourceMatches(resource)

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
	case "intervieweb", "typify", "universia", "talentreef":
		return tenthMonitorGone(config, resource, status, disabled)
	case "welcometothejungle", "ycombinator", "talentbrew":
		return false
	case "beehire", "hirehive", "computrabajo":
		o, e := api.NinthProviderOptionsFromMetadata(config["crawler_type"], config["board_url"], config["metadata"])
		if e != nil || disabled || !o.ResourceMatches(resource) {
			return false
		}
		if o.Provider == "beehire" {
			return resource == o.ListingURL() && status == 404
		}
		if o.Provider == "hirehive" {
			return resource == o.PageURL(1) && (status == 404 || status == 410)
		}
		return status == 404 || status == 410
	case "earcu", "cvwarehouse":
		return false
	case "woowa":
		o, e := api.EighthProviderOptionsFromMetadata("woowa", config["board_url"], config["metadata"])
		return e == nil && resource == o.WoowaPageURL(0) && (status == 404 || status == 410) && !disabled
	case "deel", "hibob", "traffit":
		return false
	case "jobs_ch":
		return false
	case "recruiterbox":
		o, e := api.RecruiterboxOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && o.ResourceMatches(resource) && (resource == o.PageURL(1) && (status == 404 || status == 410) && !disabled || status == 200 && disabled)
	case "manatal":
		return false
	case "hrmos":
		o, e := api.HRMOSOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && resource == o.ListingURL(1) && (status == 404 || status == 410) && !disabled
	case "dayforce":
		board, _, e := api.DayforceOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && board.ResourceMatches(resource) && resource != board.SearchURL() && ((status == 404 || status == 410) && !disabled || status == 200 && disabled)
	case "adp":
		o, e := api.ADPOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && resource == o.SearchURL(1) && (status == 404 || status == 410) && !disabled
	case "cornerstone":
		o, e := api.CornerstoneOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && resource == o.ListingURL() && (status == 404 || status == 410) && !disabled
	case "paylocity":
		o, e := api.PaylocityOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && resource == o.Listing && (status == 404 || status == 410) && !disabled
	case "paycom":
		o, e := api.PaycomOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && resource == o.PortalURL() && ((status == 404 || status == 410) && !disabled || status == 200 && disabled)
	case "rippling":
		return false

	case "comeet":
		o, e := api.ComeetOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && o.API && resource == o.Endpoint && status == 404 && !disabled
	case "jobvite":
		o, e := api.JobviteOptionsFromMetadata(config["board_url"], config["metadata"])
		return e == nil && resource == o.Endpoint && ((status == 404 || status == 410) && !disabled || disabled && (status == 301 || status == 302 || status == 303 || status == 307 || status == 308))

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
