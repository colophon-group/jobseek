package queue

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
)

var sfMetadataKey = regexp.MustCompile(`^[a-z][a-z0-9_]{0,63}$`)
var sfPropertyID = regexp.MustCompile(`^[a-z][a-z0-9_-]{0,63}$`)

// Explicit fields are required. Historic fetch_company remains optional.
func RSSDetailFields(config map[string]string) (map[string]string, map[string]bool, error) {
	md, err := richProfileMetadata(config)
	if err != nil {
		return nil, nil, err
	}
	fields := map[string]string{}
	required := map[string]bool{}
	if raw, exists := md["fetch_company"]; exists && string(raw) != "null" {
		var enabled bool
		if json.Unmarshal(raw, &enabled) != nil {
			return nil, nil, ErrUnsupportedProfile
		}
		if enabled {
			fields["company"] = "customfield1"
		}
	}
	if raw, exists := md["detail_fields"]; exists && string(raw) != "null" {
		values, err := profileMetadataFields(string(raw), nil)
		if err != nil || len(values) > 16 {
			return nil, nil, ErrUnsupportedProfile
		}
		for key, raw := range values {
			var property string
			if !sfMetadataKey.MatchString(key) || json.Unmarshal(raw, &property) != nil || !sfPropertyID.MatchString(property) {
				return nil, nil, ErrUnsupportedProfile
			}
			fields[key] = property
			required[key] = true
		}
	}
	var preset string
	if len(fields) > 0 && (json.Unmarshal(md["preset"], &preset) != nil || preset != "successfactors") {
		return nil, nil, ErrUnsupportedProfile
	}
	return fields, required, nil
}

func RSSDetailMonitorResourceMatches(profile GreenhouseMonitorProfile, resource string) bool {
	if profile.Provider != "rss" || !profile.RSSDetailEnrichment || !validGreenhouseResponseResource(resource) {
		return false
	}
	root, e1 := url.Parse(profile.Endpoint)
	u, e2 := url.Parse(resource)
	return e1 == nil && e2 == nil && root.Scheme == "https" && u.Scheme == "https" && strings.EqualFold(strings.TrimRight(root.Hostname(), "."), strings.TrimRight(u.Hostname(), ".")) && (u.Port() == "" || u.Port() == "443") && u.User == nil && u.Fragment == ""
}
