package queue

import (
	"encoding/json"
	"net/url"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

const seekDetailProfile = "seek.graphql-detail/v1"

// Admission derives the public advertiser and market from the canonical board.
// Each posting still resolves its actual URL under the existing claim barriers.
func inspectSeekDetail(boardID string, config map[string]string, source string, worker WorkerType, ownership bool) (WorkdayDetailProfile, error) {
	fail := func() (WorkdayDetailProfile, error) { return WorkdayDetailProfile{}, ErrUnsupportedProfile }
	if worker != Simple || config["scraper_needs_browser"] != "0" || config["crawler_type"] != "seek" {
		return fail()
	}
	monitor, err := InspectRichMonitor(boardID, config)
	if err != nil || monitor.Provider != "seek" {
		return fail()
	}
	board, err := api.SeekOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil {
		return fail()
	}
	md, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return fail()
	}
	var scraper string
	if json.Unmarshal(md["scraper_type"], &scraper) != nil || scraper != "seek" {
		return fail()
	}
	options := map[string]any{}
	if raw, exists := md["scraper_config"]; exists && string(raw) != "null" {
		fields, err := profileMetadataFields(string(raw), map[string]bool{"advertiser_id": true})
		if err != nil {
			return fail()
		}
		for key, raw := range fields {
			var value string
			if json.Unmarshal(raw, &value) != nil || value != board.Advertiser {
				return fail()
			}
			options[key] = value
		}
	}
	// The canonical monitor identity supplies the binding when the legacy scraper
	// did not repeat it. A foreign advertiser response can never become a write.
	options["advertiser_id"] = board.Advertiser
	if ownership {
		source = "https://" + board.JobHost + "/job/1"
	}
	detail, err := api.SeekDetailOptionsForSource(source, options)
	endpoint, parseErr := url.Parse(detail.Request.URL)
	if err != nil || parseErr != nil || endpoint.Hostname() != board.JobHost {
		return fail()
	}
	p := WorkdayDetailProfile{BoardID: boardID, CompanyID: monitor.CompanyID, SourceURL: source, Endpoint: detail.Request.URL, Domain: board.JobHost, Profile: seekDetailProfile, EffectiveBoardSHA256: monitor.EffectiveConfigSHA256, HTTPAPIConfig: options}
	if ownership {
		p.SourceURL = config["board_url"]
		p.Domain = "*"
	}
	return p, nil
}

func InspectSeekDetail(boardID string, config map[string]string, source string, worker WorkerType) (WorkdayDetailProfile, error) {
	return inspectSeekDetail(boardID, config, source, worker, false)
}
