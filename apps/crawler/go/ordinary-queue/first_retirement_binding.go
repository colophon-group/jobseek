package queue

import "encoding/json"

// Avature's legacy monitor publishes its discovered portal ID independently of
// the configured DOM detail scraper. Cold retirement can revoke the original
// detail owner after that one addition, while runtime claims still require the
// exact original hash. Every other immutable field and the current SQL/cache
// agreement remain checked by the existing inspectors.
func firstRetirementDetailHashMatches(binding ownershipDetail, expected string, current WorkdayDetailProfile, canonical map[string]string) bool {
	if current.EffectiveBoardSHA256 == expected {
		return true
	}
	if binding.Profile != domDetailProfile || binding.Config["crawler_type"] != "avature" || canonical["crawler_type"] != "avature" {
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
