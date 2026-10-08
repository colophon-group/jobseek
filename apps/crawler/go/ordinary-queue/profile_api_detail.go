package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"

	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	join "github.com/colophon-group/jobseek/apps/crawler/go/join-monitor"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	oracle "github.com/colophon-group/jobseek/apps/crawler/go/oracle-hcm"
	smartrecruiters "github.com/colophon-group/jobseek/apps/crawler/go/smartrecruiters-monitor"
	workable "github.com/colophon-group/jobseek/apps/crawler/go/workable-monitor"
)

const smartRecruitersDetailProfile = "smartrecruiters.api-detail/v1"
const workableDetailProfile = "workable.api-detail/v1"
const joinDetailProfile = "join.nextdata-detail/v1"
const oracleDetailProfile = "oracle_hcm.api-detail/v1"
const adpDetailProfile = "adp.public-detail/v1"
const paylocityDetailProfile = "paylocity.html-detail/v1"
const paylocityProxyDetailProfile = "paylocity.proxy-html-detail/v1"
const paycomDetailProfile = "paycom.public-detail/v1"
const ripplingDetailProfile = "rippling.v1-detail/v1"
const mokahrDetailProfile = "mokahr.encrypted-detail/v1"
const eightfoldDetailProfile = "eightfold.jsonld-api-detail/v1"
const eightfoldProxyDetailProfile = "eightfold.proxy-jsonld-api-detail/v1"

func independentDetailProfile(profile string) bool {
	switch profile {
	case notionDetailProfile, pdfDetailProfile:
		return true
	case adpDetailProfile, paylocityDetailProfile, paylocityProxyDetailProfile, paycomDetailProfile, ripplingDetailProfile, mokahrDetailProfile, eightfoldDetailProfile, eightfoldProxyDetailProfile, domProxyDetailProfile, jsonldProxyDetailProfile, httpAPIProxyDetailProfile, domRenderedDetailProfile, jsonldRenderedDetailProfile, embeddedRenderedDetailProfile, domDetailProfile, jsonldDetailProfile, smartRecruitersDetailProfile, workableDetailProfile, joinDetailProfile, oracleDetailProfile, embeddedDetailProfile, httpAPIDetailProfile:
		return true
	}
	return false
}

// InspectAPIDetail binds the full canonical board and its existing public API
// scraper. Independent detail ownership does not change legacy monitor identity.
func InspectAPIDetail(boardID string, config map[string]string, source string, worker WorkerType) (WorkdayDetailProfile, error) {
	fail := func() (WorkdayDetailProfile, error) { return WorkdayDetailProfile{}, ErrUnsupportedProfile }
	if worker != Simple || !canonicalUUID.MatchString(boardID) || !canonicalUUID.MatchString(config["company_id"]) || config["scraper_needs_browser"] != "0" || config["crawler_type"] == "" {
		return fail()
	}
	for key, value := range config {
		if !greenhouseBoardFields[key] || len(value) > 1<<20 || !utf8.ValidString(value) || strings.ContainsRune(value, 0) {
			return fail()
		}
	}
	if _, ok := profileInterval(config["scrape_interval_hours"], time.Hour); !ok {
		return fail()
	}
	if _, ok := profileInterval(config["check_interval_minutes"], time.Minute); !ok {
		return fail()
	}
	if len(source) > 8192 || !utf8.ValidString(source) || strings.ContainsRune(source, 0) {
		return fail()
	}
	u, err := url.Parse(source)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" || !validHost(u.Hostname()) || u.Port() != "" {
		return fail()
	}
	metadata, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return fail()
	}
	scraper := config["crawler_type"]
	var explicitScraper string
	if raw, ok := metadata["scraper_type"]; ok && string(raw) != "null" {
		if json.Unmarshal(raw, &explicitScraper) != nil {
			return fail()
		}
	}
	if explicitScraper != "" {
		scraper = explicitScraper
	}
	if scraper != "adp" && scraper != "paylocity" && scraper != "paycom" && scraper != "rippling" && scraper != "mokahr" && scraper != "eightfold" && scraper != "smartrecruiters" && scraper != "workable" && scraper != "nextdata" && scraper != "oracle_hcm" {
		return fail()
	}
	allowed := map[string]bool{"proxy": true, "render": true, "ssl_verify": true}
	if scraper == "adp" {
		allowed = map[string]bool{"enrich": true, "locale": true, "title_location_pattern": true}
	}
	if scraper == "paylocity" {
		allowed = map[string]bool{"enrich": true, "proxy": true}
	}
	if scraper == "paycom" {
		allowed = map[string]bool{"defaults": true, "enrich": true}
	}
	if scraper == "rippling" {
		allowed = map[string]bool{"slug": true}
	}
	if scraper == "mokahr" {
		allowed["locale"], allowed["enrich"] = true, true
	}
	if scraper == "eightfold" {
		allowed = nil
	} // Validate the existing JSON-LD parser options below.
	if scraper == "workable" {
		allowed["token"] = true
	}
	if scraper == "oracle_hcm" {
		allowed = map[string]bool{"host": true, "site": true, "enrich": true, "proxy": true, "fields": true, "api_url": true, "json_path": true}
	}
	if scraper == "nextdata" {
		allowed = map[string]bool{"path": true, "fields": true}
	}
	var options map[string]json.RawMessage
	if raw, ok := metadata["scraper_config"]; ok && string(raw) != "null" {
		options, err = profileMetadataFields(string(raw), allowed)
		if err != nil {
			return fail()
		}
	}
	for _, key := range []string{"proxy", "render"} {
		if (scraper == "paylocity" || scraper == "eightfold") && key == "proxy" && string(options[key]) == "true" {
			continue
		}
		if raw, ok := options[key]; ok && string(raw) != "false" && string(raw) != "null" {
			return fail()
		}
	}
	if raw, ok := options["ssl_verify"]; ok && string(raw) != "true" && string(raw) != "null" {
		return fail()
	}
	var endpoint, profile, override string
	var enrichmentFields []string
	var oracleFields map[string]any
	var jsonldOptions map[string]any
	var locale string
	var providerOptions map[string]any
	if scraper == "adp" || scraper == "paylocity" {
		providerOptions = map[string]any{}
		raw, _ := json.Marshal(options)
		if json.Unmarshal(raw, &providerOptions) != nil {
			return fail()
		}
		enrichmentFields, err = monitorEnrichmentFields(config, map[string]bool{"title": true, "description": true, "locations": true, "employment_type": true, "job_location_type": true, "date_posted": true, "base_salary": true})
		if scraper == "adp" {
			o, e := apisniffer.ADPDetailRoute(source, providerOptions)
			if e != nil {
				return fail()
			}
			endpoint, profile = o.DetailURL(), adpDetailProfile
		} else {
			if apisniffer.PaylocityDetailRoute(source) != nil {
				return fail()
			}
			endpoint, profile = source, paylocityDetailProfile
			if string(options["proxy"]) == "true" {
				profile = paylocityProxyDetailProfile
			}
		}
	} else if scraper == "paycom" {
		o, _, e := apisniffer.PaycomDetailRoute(source)
		if e != nil {
			return fail()
		}
		providerOptions = map[string]any{}
		body, _ := json.Marshal(options)
		if json.Unmarshal(body, &providerOptions) != nil {
			return fail()
		}
		if _, e := apisniffer.PaycomDefaultLocations(providerOptions); e != nil {
			return fail()
		}
		endpoint, profile = o.PortalURL(), paycomDetailProfile
		enrichmentFields, err = paycomEnrichmentFields(config)
	} else if scraper == "rippling" {
		if raw, present := options["slug"]; present && string(raw) != "null" {
			if json.Unmarshal(raw, &override) != nil {
				return fail()
			}
		}
		o, id, e := apisniffer.RipplingDetailRoute(source, override)
		if e != nil {
			return fail()
		}
		endpoint, profile = o.DetailURL(id), ripplingDetailProfile
	} else if scraper == "mokahr" {
		route, e := apisniffer.MokahrDetailRouteForSource(source)
		if e != nil {
			return fail()
		}
		locale = "zh-CN"
		if raw, present := options["locale"]; present {
			if string(raw) == "null" || json.Unmarshal(raw, &locale) != nil || len(locale) < 2 || len(locale) > 32 || strings.ContainsAny(locale, "\x00\r\n") {
				return fail()
			}
		}
		endpoint, profile = route.APIURL(), mokahrDetailProfile
		enrichmentFields, err = providerBatchEnrichment(config)
	} else if scraper == "eightfold" {
		route, e := apisniffer.EightfoldDetailRouteForSource(source)
		if e != nil {
			return fail()
		}
		jsonldOptions = map[string]any{}
		for key, raw := range options {
			var value any
			if json.Unmarshal(raw, &value) != nil {
				return fail()
			}
			jsonldOptions[key] = value
		}
		if string(options["proxy"]) == "true" {
			jsonldOptions["proxy"] = false
		}
		if jsonld.ValidateConfig(jsonldOptions) != nil {
			return fail()
		}
		// Eightfold's legacy fast path uses parse_html directly: transport,
		// fallback and description-selector behavior belongs to other scrapers.
		for _, key := range []string{"request_headers", "description_selector", "transport_attempts", "iframe_src"} {
			if _, present := options[key]; present {
				return fail()
			}
		}
		if raw, present := options["fallback"]; present && string(raw) != "null" && string(raw) != "[]" {
			return fail()
		}
		endpoint, profile = route.APIURL, eightfoldDetailProfile
		if string(options["proxy"]) == "true" {
			profile = eightfoldProxyDetailProfile
		}
		enrichmentFields, err = providerBatchEnrichment(config)
	} else if scraper == "smartrecruiters" {
		_, endpoint, err = smartrecruiters.DetailEndpoint(source)
		profile = smartRecruitersDetailProfile
	} else if scraper == "workable" {
		if raw, ok := options["token"]; ok && string(raw) != "null" && json.Unmarshal(raw, &override) != nil {
			return fail()
		}
		endpoint, _, err = workable.DetailEndpoints(source, override)
		profile = workableDetailProfile
	} else if scraper == "oracle_hcm" {
		var overrides map[string]any
		body, _ := json.Marshal(options)
		if json.Unmarshal(body, &overrides) != nil {
			return fail()
		}
		if raw, ok := options["fields"]; ok && string(raw) != "null" {
			if json.Unmarshal(raw, &oracleFields) != nil {
				return fail()
			}
			for target, spec := range oracleFields {
				switch target {
				case "title", "locations", "date_posted", "description", "valid_through", "employment_type", "job_location_type", "language":
				default:
					return fail()
				}
				if apisniffer.ValidateField(spec) != nil {
					return fail()
				}
			}
		}
		endpoint, err = oracle.DetailEndpoint(source, overrides)
		if err != nil {
			return fail()
		}
		profile = oracleDetailProfile
		enrichmentFields, err = oracleMonitorEnrichment(config)
		if err != nil {
			return fail()
		}
		// Legacy detail auto-resolution inherits its Oracle default for null
		// configs; monitor scheduling intentionally distinguishes null/absent.
		if explicitScraper == "" && string(metadata["scraper_config"]) == "null" {
			enrichmentFields = []string{"description"}
		}
	} else {
		err = join.ValidateDetailURL(source)
		if err == nil {
			var fields map[string]string
			fields, err = join.ValidateDetailConfig(options)
			if err == nil && len(fields) == 0 {
				return fail()
			}
		}
		endpoint, profile = source, joinDetailProfile
	}
	if err != nil {
		return fail()
	}
	stable, err := stableJSONLDConfig(config, metadata)
	if err != nil {
		return fail()
	}
	body, err := json.Marshal(struct {
		BoardID string            `json:"board_id"`
		Config  map[string]string `json:"config"`
	}{boardID, stable})
	if err != nil {
		return fail()
	}
	digest := sha256.Sum256(body)
	p := WorkdayDetailProfile{BoardID: boardID, CompanyID: config["company_id"], SourceURL: source, Endpoint: endpoint, Domain: strings.ToLower(u.Hostname()), Profile: profile, EffectiveBoardSHA256: hex.EncodeToString(digest[:]), APITokenOverride: override, EnrichmentFields: enrichmentFields, OracleFields: oracleFields}
	p.APILocale, p.JSONLDConfig = locale, jsonldOptions
	p.HTTPAPIConfig = providerOptions
	if profile == joinDetailProfile {
		p.JoinDetailConfig = options
	}
	return p, nil
}

func inspectAPIDetailOwnership(boardID string, config map[string]string) (WorkdayDetailProfile, error) {
	metadata, err := profileMetadataFields(config["metadata"], nil)
	if err == nil {
		if o, e := apisniffer.PaycomOptionsFromMetadata(config["board_url"], config["metadata"]); e == nil {
			if p, e := InspectAPIDetail(boardID, config, o.JobURL("1"), Simple); e == nil && p.Profile == paycomDetailProfile {
				p.Domain = "*"
				return p, nil
			}
		}
		if o, e := apisniffer.RipplingOptionsFromMetadata(config["board_url"], config["metadata"]); e == nil {
			if p, e := InspectAPIDetail(boardID, config, o.JobURL("OWNERSHIPADMISSION"), Simple); e == nil && p.Profile == ripplingDetailProfile {
				p.Domain = "*"
				return p, nil
			}
		}
		if o, e := apisniffer.MokahrOptionsFromMetadata(config["board_url"], config["metadata"]); e == nil {
			if p, e := InspectAPIDetail(boardID, config, o.Partitions[0].JobURL("OWNERSHIPADMISSION"), Simple); e == nil && p.Profile == mokahrDetailProfile {
				p.Domain = "*"
				return p, nil
			}
		}
		if source, e := url.Parse(config["board_url"]); e == nil && source.Scheme == "https" && source.Host != "" {
			candidate := "https://" + source.Host + "/careers/job/1?domain=" + url.QueryEscape(source.Hostname())
			if p, e := InspectAPIDetail(boardID, config, candidate, Simple); e == nil && (p.Profile == eightfoldDetailProfile || p.Profile == eightfoldProxyDetailProfile) {
				p.Domain = "*"
				return p, nil
			}
		}
		var options map[string]any
		if raw, ok := metadata["scraper_config"]; ok {
			_ = json.Unmarshal(raw, &options)
		}
		if o, err := oracle.OptionsFromMetadata(config["board_url"], map[string]any{"host": options["host"], "site": options["site"]}); err == nil {
			if p, err := InspectAPIDetail(boardID, config, o.JobURL("OWNERSHIPADMISSION"), Simple); err == nil && p.Profile == oracleDetailProfile {
				p.Domain = "*"
				return p, nil
			}
		}
	}
	for _, source := range []string{"https://2000recruiting.paylocity.com/Recruiting/Jobs/Details/1", "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=01234567-89ab-cdef-0123-456789abcdef&ccId=19000101_000001&lang=en_US&jobId=1_1", "https://ats.rippling.com/native/jobs/OWNERSHIPADMISSION", "https://www.paycomonline.net/v4/ats/web.php/portal/11111111111111111111111111111111/jobs/1", "https://app.mokahr.com/social-recruitment/native/1#/job/OWNERSHIPADMISSION", "https://jobs.smartrecruiters.com/native/OWNERSHIPADMISSION", "https://apply.workable.com/native/j/OWNERSHIPADMISSION/", "https://join.com/companies/native/OWNERSHIPADMISSION"} {
		if profile, err := InspectAPIDetail(boardID, config, source, Simple); err == nil {
			profile.Domain = "*"
			return profile, nil
		}
	}
	return WorkdayDetailProfile{}, ErrUnsupportedProfile
}
