package queue

import (
	"encoding/json"
	"net/url"
	"strings"
	"unicode/utf8"

	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
)

// WorkdayDetailProfile is detached admission evidence, never a claim or write
// grant. Detail ownership must separately bind the canonical posting and board.
type WorkdayDetailProfile struct {
	BoardID, CompanyID, SourceURL, Endpoint, Domain string
	Profile, EffectiveBoardSHA256                   string
	FacilityTenantAliases                           []string
	EnrichmentFields                                []string
	OracleFields                                    map[string]any
	EmbeddedConfig                                  map[string]any
	HTTPAPIConfig                                   map[string]any
	EmbeddedNextdata                                bool
	JSONLDConfig                                    map[string]any
	DOMConfig                                       map[string]any
	APITokenOverride                                string
	JoinDetailConfig                                map[string]json.RawMessage
}

// Admission binds the configured tenant/domain without enumerating postings.
// Every actual pop still resolves and locks its canonical posting separately.
func inspectWorkdayDetailOwnership(boardID string, config map[string]string) (WorkdayDetailProfile, error) {
	monitor, err := InspectRichMonitor(boardID, config)
	if err != nil || monitor.Provider != "workday" {
		return WorkdayDetailProfile{}, ErrUnsupportedProfile
	}
	endpoint, err := url.Parse(monitor.Endpoint)
	if err != nil {
		return WorkdayDetailProfile{}, ErrUnsupportedProfile
	}
	parts := strings.Split(strings.Trim(endpoint.Path, "/"), "/")
	if len(parts) != 5 || parts[0] != "wday" || parts[1] != "cxs" || parts[4] != "jobs" {
		return WorkdayDetailProfile{}, ErrUnsupportedProfile
	}
	endpoint.Path = "/" + parts[3] + "/job/native-ownership-admission"
	endpoint.RawPath = ""
	return InspectWorkdayDetail(boardID, config, endpoint.String(), Simple)
}

// InspectWorkdayDetail resolves the existing explicit/inferred Workday scraper.
// Monitor throttle domains are not detail request domains. Canonical/cache and
// posting identity must be revalidated under the same barriers before a pop.
func InspectWorkdayDetail(boardID string, config map[string]string, sourceURL string, worker WorkerType) (WorkdayDetailProfile, error) {
	fail := func() (WorkdayDetailProfile, error) { return WorkdayDetailProfile{}, ErrUnsupportedProfile }
	if worker != Simple || config["scraper_needs_browser"] != "0" || !utf8.ValidString(sourceURL) {
		return fail()
	}
	monitor, err := InspectRichMonitor(boardID, config)
	if err != nil || monitor.Provider != "workday" {
		return fail()
	}
	metadata, err := richProfileMetadata(config)
	if err != nil {
		return fail()
	}
	var scraper string
	if raw, present := metadata["scraper_type"]; present && string(raw) != "null" {
		if json.Unmarshal(raw, &scraper) != nil {
			return fail()
		}
	}
	if scraper != "" && scraper != "workday" {
		return fail()
	}
	var options map[string]json.RawMessage
	if raw, present := metadata["scraper_config"]; present && string(raw) != "null" {
		// Reuse the strict object parser so duplicate transport overrides cannot
		// be interpreted differently by admission and execution.
		options, err = profileMetadataFields(string(raw), map[string]bool{"facility_tenant_aliases": true, "proxy": true, "render": true, "language": true})
		if err != nil {
			return fail()
		}
	}
	for _, key := range []string{"proxy", "render"} {
		if raw, present := options[key]; present && string(raw) != "false" && string(raw) != "null" {
			return fail()
		}
	}
	var aliases []string
	if raw, present := options["facility_tenant_aliases"]; present && string(raw) != "null" {
		if json.Unmarshal(raw, &aliases) != nil || len(aliases) > 32 {
			return fail()
		}
		for i, alias := range aliases {
			alias = strings.TrimSpace(alias)
			if alias == "" || len(alias) > 256 || !utf8.ValidString(alias) || strings.ContainsRune(alias, 0) {
				return fail()
			}
			aliases[i] = alias
		}
	}
	endpoint, _, err := workday.DetailAPIURL(sourceURL)
	if err != nil {
		return fail()
	}
	parsed, err := url.Parse(endpoint)
	monitorEndpoint, monitorErr := url.Parse(monitor.Endpoint)
	if err != nil || monitorErr != nil || parsed.Host != monitorEndpoint.Host {
		return fail()
	}
	return WorkdayDetailProfile{BoardID: boardID, CompanyID: monitor.CompanyID, SourceURL: sourceURL, Endpoint: endpoint, Domain: parsed.Hostname(), Profile: "workday.cxs-detail/v1", EffectiveBoardSHA256: monitor.EffectiveConfigSHA256, FacilityTenantAliases: aliases}, nil
}
