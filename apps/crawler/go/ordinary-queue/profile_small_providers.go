package queue

import (
	"encoding/json"
	"net/url"
	"strconv"
	"strings"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func SmallProvider(provider string) bool {
	return provider == "jobbank104" || provider == "cnstaff" || provider == "seamlesshiring" || provider == "jarvi" || provider == "job51"
}

func validJob51SourceIdentity(config map[string]string, source, identity string) bool {
	parts := strings.Split(identity, ":")
	if len(parts) != 3 || parts[0] != "job51" || len(identity) > 64 {
		return false
	}
	o, err := api.SmallProviderOptionsFromMetadata("job51", config["board_url"], config["metadata"])
	if err != nil || parts[1] != strconv.FormatInt(o.CTMID, 10) {
		return false
	}
	if _, err = api.Job51DetailRequest(parts[2]); err != nil {
		return false
	}
	return source == "https://jobs.51job.com/all/"+parts[2]+".html"
}
func inspectSmallProviderMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	if config["monitor_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	fields, err := smallProviderMonitorEnrichment(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	var scraper string
	if config["crawler_type"] == "jobbank104" && json.Unmarshal(md["scraper_type"], &scraper) == nil && scraper == "api_sniffer" {
		// The independently scheduled API reader must retain an explicit,
		// supported field mask and its complete HTTP request validation.
		if len(fields) == 0 {
			return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
		}
		if _, err := inspectHTTPAPIDetail(boardID, config, config["board_url"], Simple, true); err != nil {
			return GreenhouseMonitorProfile{}, err
		}
	} else if err := feedRichDetailAssignment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	o, err := api.SmallProviderOptionsFromMetadata(config["crawler_type"], config["board_url"], config["metadata"])
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, o.Provider, o.Profile(), o.Provider, o.ListingURL())
}
func smallProviderMonitorGone(config map[string]string, resource string, status int, disabled bool) bool {
	if config["crawler_type"] == "seamlesshiring" || config["crawler_type"] == "jarvi" || disabled || status != 404 && status != 410 {
		return false
	}
	o, e := api.SmallProviderOptionsFromMetadata(config["crawler_type"], config["board_url"], config["metadata"])
	if e != nil || !o.ResourceMatches(resource) {
		return false
	}
	if o.Provider == "job51" {
		return true
	}
	u, e := url.Parse(resource)
	if e != nil {
		return false
	}
	u.RawQuery = ""
	return u.String() == o.ListingURL()
}

// Jobbank104 listing fields remain authoritative while its explicit API detail
// assignment supplements the full description. Other providers retain their
// existing assignment validation.
func smallProviderMonitorEnrichment(config map[string]string) ([]string, error) {
	md, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return nil, err
	}
	var scraper string
	if json.Unmarshal(md["scraper_type"], &scraper) != nil {
		return nil, ErrUnsupportedProfile
	}
	allowed := map[string]bool{}
	if config["crawler_type"] == "jobbank104" && scraper == "api_sniffer" {
		allowed["description"] = true
	}
	return monitorEnrichmentFields(config, allowed)
}
