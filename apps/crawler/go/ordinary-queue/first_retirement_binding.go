package queue

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
)

// Avature's legacy monitor publishes its discovered portal ID independently of
// the configured DOM detail scraper. Cold retirement can revoke the original
// detail owner after that one addition, while runtime claims still require the
// exact original hash. Every other immutable field and the current SQL/cache
// agreement remain checked by the existing inspectors.
func firstRetirementDetailHashMatches(binding ownershipDetail, expected string, current WorkdayDetailProfile, canonical map[string]string) bool {
	if current.EffectiveBoardSHA256 == expected {
		return true
	}
	if binding.Profile == embeddedDetailProfile && binding.Config["crawler_type"] == "ukg" && canonical["crawler_type"] == "ukg" {
		return firstUKGRetirementDetailHashMatches(binding, expected, current, canonical)
	}
	if (binding.Profile != domDetailProfile && binding.Profile != domProxyDetailProfile) || binding.Config["crawler_type"] != "avature" || canonical["crawler_type"] != "avature" {
		return false
	}
	previous, err := profileMetadataFields(binding.Config["metadata"], nil)
	if err != nil {
		return false
	}
	if _, present := previous["portal_id"]; present {
		return false
	}
	metadata, err := profileMetadataFields(canonical["metadata"], nil)
	if err != nil {
		return false
	}
	var portal string
	if json.Unmarshal(metadata["portal_id"], &portal) != nil || len(portal) == 0 || len(portal) > 128 {
		return false
	}
	positive := false
	for _, digit := range portal {
		if digit < '0' || digit > '9' {
			return false
		}
		positive = positive || digit != '0'
	}
	if !positive {
		return false
	}
	delete(metadata, "portal_id")
	body, err := json.Marshal(metadata)
	if err != nil {
		return false
	}
	prior := cloneConfig(canonical)
	prior["metadata"] = string(body)
	profile, err := inspectDetailOwnership(binding.BoardID, prior)
	return err == nil && profile.Profile == binding.Profile && profile.Domain == binding.Domain && profile.CompanyID == current.CompanyID && profile.EffectiveBoardSHA256 == expected
}

var retirementUKGHost = regexp.MustCompile(`^(?:recruiting(?:[2-9])?\.ultipro\.com|recruiting\.ultipro\.ca|[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.rec\.pro\.ukg\.net)$`)
var retirementUKGListingPath = regexp.MustCompile(`^/([A-Za-z0-9]{3,64})/JobBoard/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/?$`)

// UKG publishes the listing URL and identifiers after its first search page.
// Only additions exactly derivable from the originally bound board URL may
// retire that detail owner. Runtime claims continue to reject the changed hash.
func firstUKGRetirementDetailHashMatches(binding ownershipDetail, expected string, current WorkdayDetailProfile, canonical map[string]string) bool {
	u, err := url.Parse(binding.Config["board_url"])
	if err != nil || u.Scheme != "https" || u.User != nil || u.Port() != "" || u.RawQuery != "" || u.Fragment != "" || u.Opaque != "" || u.RawPath != "" || !retirementUKGHost.MatchString(strings.ToLower(u.Hostname())) {
		return false
	}
	parts := retirementUKGListingPath.FindStringSubmatch(u.Path)
	if parts == nil {
		return false
	}
	host, tenant, board := strings.ToLower(u.Hostname()), parts[1], strings.ToLower(parts[2])
	derived := map[string]string{"host": host, "tenant": tenant, "board_id": board, "listing_url": "https://" + host + "/" + tenant + "/JobBoard/" + board}
	previous, err := profileMetadataFields(binding.Config["metadata"], nil)
	if err != nil {
		return false
	}
	metadata, err := profileMetadataFields(canonical["metadata"], nil)
	if err != nil {
		return false
	}
	added := false
	for key, value := range derived {
		raw, present := metadata[key]
		if !present {
			continue
		}
		var actual string
		if json.Unmarshal(raw, &actual) != nil || actual != value {
			return false
		}
		if _, configured := previous[key]; !configured {
			delete(metadata, key)
			added = true
		}
	}
	if !added {
		return false
	}
	body, err := json.Marshal(metadata)
	if err != nil {
		return false
	}
	prior := cloneConfig(canonical)
	prior["metadata"] = string(body)
	profile, err := inspectDetailOwnership(binding.BoardID, prior)
	return err == nil && profile.Profile == binding.Profile && profile.Domain == binding.Domain && profile.CompanyID == current.CompanyID && profile.EffectiveBoardSHA256 == expected
}
