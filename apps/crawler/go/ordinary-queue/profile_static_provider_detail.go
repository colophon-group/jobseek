package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

const linkedInDetailProfile = "linkedin.guest-detail/v1"
const jazzHRDetailProfile = "jazzhr.public-detail/v1"
const taleoEnterpriseDetailProfile = "taleo.enterprise-detail/v1"
const jobConvoDetailProfile = "jobconvo.public-detail/v1"

// These detail scrapers are independent of the canonical board's monitor.
// Every claimed posting is resolved against its actual source before I/O.
func inspectStaticProviderDetail(boardID string, config map[string]string, source string, worker WorkerType, ownership bool) (WorkdayDetailProfile, error) {
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
	metadata, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return fail()
	}
	var scraper string
	if json.Unmarshal(metadata["scraper_type"], &scraper) != nil {
		return fail()
	}
	profile := map[string]string{"linkedin": linkedInDetailProfile, "jazzhr": jazzHRDetailProfile, "taleo": taleoEnterpriseDetailProfile, "jobconvo": jobConvoDetailProfile, "paycor": "paycor.public-detail/v1", "recruiterbox": "recruiterbox.public-detail/v1", "veryeast": "veryeast.public-detail/v1", "onlyfy": "onlyfy.public-detail/v1", "tupu360": "tupu360.public-detail/v1", "phuketall": "phuketall.public-detail/v1"}[scraper]
	if profile == "" {
		return fail()
	}
	locale := "pt-br"
	optionsConfig := map[string]any{}
	if raw, ok := metadata["scraper_config"]; ok && string(raw) != "null" {
		allowed := map[string]bool{"enrich": true}
		if scraper == "onlyfy" {
			allowed["language"] = true
		}
		if scraper == "tupu360" {
			allowed["listing_enrichment"] = true
		}
		if scraper == "jobconvo" {
			allowed = map[string]bool{"locale": true}
		}
		fields, err := profileMetadataFields(string(raw), allowed)
		if err != nil {
			return fail()
		}
		if scraper == "jobconvo" {
			if value, present := fields["locale"]; present {
				if json.Unmarshal(value, &locale) != nil {
					return fail()
				}
			}
		} else if scraper == "onlyfy" || scraper == "tupu360" || scraper == "phuketall" {
			for key, raw := range fields {
				var value any
				if json.Unmarshal(raw, &value) != nil {
					return fail()
				}
				if key == "language" && value != nil {
					if _, ok := value.(string); !ok {
						return fail()
					}
				}
				if key == "listing_enrichment" {
					if _, ok := value.(bool); !ok {
						return fail()
					}
				}
				optionsConfig[key] = value
			}
		} else if scraper != "linkedin" && len(fields) != 0 {
			return fail()
		}
	}
	enrich, err := monitorEnrichmentFields(config, map[string]bool{"description": true, "employment_type": true, "job_location_type": true})
	if err != nil || scraper != "linkedin" && scraper != "onlyfy" && scraper != "tupu360" && scraper != "phuketall" && len(enrich) != 0 {
		return fail()
	}
	if ownership {
		if scraper == "onlyfy" {
			board, err := url.Parse(config["board_url"])
			if err != nil || board.Scheme != "https" || board.User != nil || board.Port() != "" || !strings.HasSuffix(strings.ToLower(board.Hostname()), ".onlyfy.jobs") {
				return fail()
			}
		}
		source = map[string]string{"linkedin": "https://www.linkedin.com/jobs/view/1", "jazzhr": "https://fixture.applytojob.com/apply/jobs/details/1", "taleo": "https://fixture.taleo.net/careersection/2/jobdetail.ftl?job=1", "jobconvo": "https://app.jobconvo.com/job/fixture/11111111-2222-3333-4444-555555555555/", "paycor": "https://recruitingbypaycor.com/career/JobIntroduction.action?id=fixture", "recruiterbox": "https://fixture.hire.trakstar.com/jobs/fixture/", "veryeast": "https://job.veryeast.cn/1/1", "onlyfy": "https://fixture.onlyfy.jobs/en/job/fixture", "tupu360": "https://fixture.tupu360.com/fixture/position/detail?positionId=0123456789abcdef01234567", "phuketall": "https://www.phuketall.com/jobs/057289-123456-phuket/fixture.html"}[scraper]
	}
	if !utf8.ValidString(source) || strings.ContainsRune(source, 0) {
		return fail()
	}
	options, err := api.StaticProviderDetailOptionsForSource(scraper, source)
	posting, parseErr := url.Parse(source)
	if err != nil || parseErr != nil {
		return fail()
	}
	if scraper == "onlyfy" {
		language, _ := optionsConfig["language"].(string)
		candidate, _, _, err := api.OnlyfyDetailURLs(source, language)
		if err != nil {
			return fail()
		}
		options.Endpoint = candidate
	}
	if scraper == "jobconvo" {
		request, _, err := api.JobConvoDetailRequest(source, locale)
		if err != nil {
			return fail()
		}
		options.Endpoint = request.URL
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
	// Queue ownership follows the posting's existing source domain. LinkedIn's
	// fixed www guest endpoint may differ from a localized posting host.
	p := WorkdayDetailProfile{BoardID: boardID, CompanyID: config["company_id"], SourceURL: source, Endpoint: options.Endpoint, Domain: strings.ToLower(posting.Hostname()), Profile: profile, EffectiveBoardSHA256: hex.EncodeToString(digest[:]), EnrichmentFields: enrich, HTTPAPIConfig: optionsConfig}
	if scraper == "jobconvo" {
		p.APILocale = strings.ToLower(locale)
	}
	if ownership {
		p.SourceURL = config["board_url"]
		p.Domain = "*"
	}
	return p, nil
}

func InspectStaticProviderDetail(boardID string, config map[string]string, source string, worker WorkerType) (WorkdayDetailProfile, error) {
	return inspectStaticProviderDetail(boardID, config, source, worker, false)
}
