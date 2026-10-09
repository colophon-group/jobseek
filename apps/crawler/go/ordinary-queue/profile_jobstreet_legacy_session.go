package queue

import (
	"encoding/json"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

const jobStreetMonitorProfile = "jobstreet.company-items/v1"
const jobStreetDetailProfile = "jobstreet.graphql-detail/v1"
const legacySFSessionProfile = "rss.successfactors-legacy-session-items/v1"

func jobStreetEnrichment(config map[string]string) ([]string, error) {
	return monitorEnrichmentFields(config, map[string]bool{"title": true, "description": true, "locations": true, "employment_type": true, "date_posted": true, "base_salary": true})
}
func inspectJobStreetMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	fail := func() (GreenhouseMonitorProfile, error) { return GreenhouseMonitorProfile{}, ErrUnsupportedProfile }
	if config["crawler_type"] != "jobstreet" || config["monitor_needs_browser"] != "0" {
		return fail()
	}
	o, e := api.JobStreetOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil || o.OrganisationID == "" {
		return fail()
	}
	var scraper string
	if json.Unmarshal(md["scraper_type"], &scraper) != nil || scraper != "jobstreet" {
		return fail()
	}
	if _, e := jobStreetEnrichment(config); e != nil {
		return fail()
	}
	if raw, ok := md["scraper_config"]; ok && string(raw) != "null" {
		if _, e := profileMetadataFields(string(raw), map[string]bool{"enrich": true}); e != nil {
			return fail()
		}
	}
	return inspectURLOnlyMonitor(boardID, config, md, "jobstreet", jobStreetMonitorProfile, o.CompanyID, o.PageRequest(1).URL)
}
func inspectJobStreetDetail(boardID string, config map[string]string, source string, worker WorkerType, ownership bool) (WorkdayDetailProfile, error) {
	fail := func() (WorkdayDetailProfile, error) { return WorkdayDetailProfile{}, ErrUnsupportedProfile }
	if worker != Simple || config["scraper_needs_browser"] != "0" {
		return fail()
	}
	monitor, e := InspectRichMonitor(boardID, config)
	if e != nil || monitor.Profile != jobStreetMonitorProfile {
		return fail()
	}
	o, e := api.JobStreetOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil {
		return fail()
	}
	fields, e := jobStreetEnrichment(config)
	if e != nil {
		return fail()
	}
	if ownership {
		source = "https://" + o.Host + "/job/1"
	}
	request, host, _, e := api.JobStreetDetailRequest(source, nil)
	if e != nil || host != o.Host {
		return fail()
	}
	p := WorkdayDetailProfile{BoardID: boardID, CompanyID: monitor.CompanyID, SourceURL: source, Endpoint: request.URL, Domain: host, Profile: jobStreetDetailProfile, EffectiveBoardSHA256: monitor.EffectiveConfigSHA256, HTTPAPIConfig: map[string]any{}, EnrichmentFields: fields}
	if ownership {
		p.SourceURL = config["board_url"]
		p.Domain = "*"
	}
	return p, nil
}
func inspectLegacySFSessionMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if config["monitor_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	o, e := api.SuccessFactorsLegacyOptionsFromMetadata(config["board_url"], config["metadata"])
	if e != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	var scraper string
	if json.Unmarshal(md["scraper_type"], &scraper) != nil || scraper != "dom" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, e := monitorEnrichmentFields(config, map[string]bool{"description": true, "locations": true}); e != nil {
		return GreenhouseMonitorProfile{}, e
	}
	return inspectURLOnlyMonitor(boardID, config, md, "rss", legacySFSessionProfile, o.Company, o.ListingURL())
}
func LegacySFSessionResourceMatches(p GreenhouseMonitorProfile, config map[string]string, resource string) bool {
	o, e := api.SuccessFactorsLegacyOptionsFromMetadata(config["board_url"], config["metadata"])
	return e == nil && p.Provider == "rss" && p.Profile == legacySFSessionProfile && p.Endpoint == o.ListingURL() && o.ResourceMatches(resource)
}

func legacySFSessionProfileResourceMatches(p GreenhouseMonitorProfile, resource string) bool {
	if p.Profile != legacySFSessionProfile || p.Provider != "rss" {
		return false
	}
	o, e := api.SuccessFactorsLegacyOptionsFromMetadata(p.Endpoint, `{"preset":"successfactors","variant":"legacy"}`)
	return e == nil && p.Endpoint == o.ListingURL() && o.Company == p.Token && o.ResourceMatches(resource)
}
