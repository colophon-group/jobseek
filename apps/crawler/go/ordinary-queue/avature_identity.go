package queue

import (
	"encoding/json"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

// Learned listing identity must already be bound at admission. Discovery can
// confirm it, including on a truncated run, but cannot rewrite operator config
// or make its next claim lose the installed ownership binding.
func validateAvatureIdentityUpdate(config map[string]string, current, patch map[string]any) error {
	if config["crawler_type"] != "avature" || len(patch) != 2 {
		return ErrConfiguration
	}
	listing, listingOK := patch["listing_url"].(string)
	portal, portalOK := patch["portal_id"].(string)
	o, err := api.AvatureOptionsFromMetadata(config["board_url"], config["metadata"])
	if err != nil || !listingOK || !portalOK || listing != o.Board.ListingURL() || portal != o.PortalID || portal == "" {
		return ErrAuthorityLost
	}
	before := cloneConfig(config)
	raw, err := json.Marshal(current)
	if err != nil {
		return ErrConfiguration
	}
	before["metadata"] = string(raw)
	a, err := InspectRichMonitor(profileBoardIDForValidation, before)
	if err != nil {
		return err
	}
	updated := make(map[string]any, len(current))
	for key, value := range current {
		updated[key] = value
	}
	for key, value := range patch {
		updated[key] = value
	}
	raw, err = json.Marshal(updated)
	if err != nil {
		return ErrConfiguration
	}
	after := cloneConfig(config)
	after["metadata"] = string(raw)
	b, err := InspectRichMonitor(profileBoardIDForValidation, after)
	if err != nil || a.EffectiveConfigSHA256 != b.EffectiveConfigSHA256 {
		return ErrAuthorityLost
	}
	return nil
}

const profileBoardIDForValidation = "00000000-0000-4000-8000-000000000001"
