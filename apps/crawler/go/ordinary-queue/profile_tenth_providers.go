package queue

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TenthProvider(provider string) bool {
	return provider == "intervieweb" || provider == "typify" || provider == "universia" || provider == "talentreef"
}

func inspectTenthMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	provider := config["crawler_type"]
	if config["monitor_needs_browser"] != "0" {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	allowed := map[string]bool{}
	if provider == "typify" {
		allowed["description"] = true
	}
	if _, err := monitorEnrichmentFields(config, allowed); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	if provider == "universia" || provider == "talentreef" {
		if err := feedRichDetailAssignment(config); err != nil {
			return GreenhouseMonitorProfile{}, err
		}
	}
	o, err := api.TenthProviderOptionsFromMetadata(provider, config["board_url"], config["metadata"])
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	return inspectURLOnlyMonitor(boardID, config, md, provider, o.Profile(), provider, o.ListingURL())
}

func tenthMonitorGone(config map[string]string, resource string, status int, disabled bool) bool {
	provider := config["crawler_type"]
	if provider != "universia" && provider != "talentreef" || disabled || status != 404 && status != 410 {
		return false
	}
	o, err := api.TenthProviderOptionsFromMetadata(provider, config["board_url"], config["metadata"])
	if err != nil || !o.ResourceMatches(resource) {
		return false
	}
	u, err := url.Parse(resource)
	if err != nil {
		return false
	}
	u.RawQuery = ""
	return u.String() == o.ListingURL()
}

func validTenthSourceIdentity(provider, source, identity string) bool {
	if len(identity) > 1024 || strings.ContainsAny(identity, "\x00\r\n") {
		return false
	}
	parts := strings.SplitN(identity, ":", 3)
	if len(parts) != 3 || parts[0] != provider {
		return false
	}
	if provider == "universia" {
		uuid := regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
		if !uuid.MatchString(parts[1]) || !uuid.MatchString(parts[2]) {
			return false
		}
		_, err := api.UniversiaJobURL(source, parts[2], parts[1])
		return err == nil
	}
	if provider != "talentreef" || !regexp.MustCompile(`^[0-9]{1,20}$`).MatchString(parts[1]) || parts[2] == "" {
		return false
	}
	u, err := url.Parse(source)
	if err != nil || u.User != nil || u.Scheme != "https" || u.Host != "apply.jobappnetwork.com" || u.RawQuery != "" || u.Fragment != "" {
		return false
	}
	prefix := "/clients/" + parts[1] + "/posting/" + parts[2] + "/"
	if !strings.HasPrefix(u.Path, prefix) {
		return false
	}
	locale := strings.TrimPrefix(u.Path, prefix)
	o, err := api.TenthProviderOptionsFromMetadata(provider, "https://apply.jobappnetwork.com/sample/"+locale, "{}")
	return err == nil && o.Locale == locale
}
