package oraclehcm

import (
	"net/url"
	"regexp"
	"strings"
)

var configuredJobPath = regexp.MustCompile(`/(?:jobs?|requisitions/preview)/([A-Za-z0-9._-]{1,128})/?$`)

// DetailEndpoint accepts Oracle's public job route or an HTTPS vanity route
// bound to explicitly configured, allowlisted Oracle tenant and site metadata.
func DetailEndpoint(jobURL string, metadata map[string]any) (string, error) {
	u, err := url.Parse(jobURL)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.Opaque != "" || u.RawPath != "" || u.Port() != "" && u.Port() != "443" {
		return "", ErrOptions
	}
	configured := map[string]any{}
	for _, key := range []string{"host", "site"} {
		if value, exists := metadata[key]; exists {
			configured[key] = value
		}
	}
	o, err := OptionsFromMetadata(jobURL, configured)
	if err != nil {
		return "", err
	}
	_, _, id, native := Candidate(jobURL, true)
	if !native {
		h, hok := normalizeHost(metadata["host"])
		s, sok := metadata["site"].(string)
		match := configuredJobPath.FindStringSubmatch(u.Path)
		if !hok || !sok || !sitePattern.MatchString(s) || len(match) != 2 || h != o.Host || s != o.Site {
			return "", ErrOptions
		}
		id = match[1]
	}
	return "https://" + o.Host + "/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id=\"" + id + "\",siteNumber=" + o.Site, nil
}

// ProjectDetail preserves the default Oracle scraper's raw fields. Description
// fallbacks skip empty strings; qualifications/responsibilities stay in Extras
// for the existing shared enrichment path rather than being appended here.
func ProjectDetail(body []byte) (map[string]any, error) {
	row, err := wrapper(body)
	if err != nil {
		return nil, err
	}
	content := map[string]any{}
	for target, source := range map[string]string{"title": "Title", "date_posted": "ExternalPostedStartDate", "employment_type": "JobSchedule"} {
		if raw := row[source]; raw != nil {
			value, ok := raw.(string)
			if !ok {
				return nil, ErrInventory
			}
			content[target] = value
		}
	}
	if raw := row["PrimaryLocation"]; raw != nil {
		value, ok := raw.(string)
		if !ok {
			return nil, ErrInventory
		}
		content["locations"] = []string{value}
	}
	for _, source := range []string{"ExternalDescriptionStr", "OrganizationDescriptionStr", "CorporateDescriptionStr"} {
		if raw := row[source]; raw != nil {
			value, ok := raw.(string)
			if !ok {
				return nil, ErrInventory
			}
			if value != "" {
				content["description"] = value
				break
			}
		}
	}
	extras := map[string]any{}
	for target, source := range map[string]string{"qualifications": "ExternalQualificationsStr", "responsibilities": "ExternalResponsibilitiesStr"} {
		if raw := row[source]; raw != nil {
			value, ok := raw.(string)
			if !ok {
				return nil, ErrInventory
			}
			if strings.TrimSpace(value) != "" {
				extras[target] = []string{value}
			}
		}
	}
	if len(extras) != 0 {
		content["extras"] = extras
	}
	return content, nil
}
