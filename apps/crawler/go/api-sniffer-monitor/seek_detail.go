package apisniffer

import (
	"encoding/json"
	"net/http"
	"net/url"
	"regexp"
	"strings"
)

type SeekDetailOptions struct {
	HTTPDetailOptions
	JobID, AdvertiserID string
}

var seekJobPath = regexp.MustCompile(`^/job/([\p{Nd}]{1,18})/?$`)

const seekDetailQuery = `query JobDetails($id: ID!) {
  jobDetails(id: $id) {
    job {
      id
      title
      content
      abstract
      location { label(locale: "{locale}") }
      advertiser { id name(locale: "{locale}") }
      workTypes { label(locale: "{locale}") }
      createdAt { dateTimeUtc }
      expiresAt { dateTimeUtc }
      isExpired
      status
      url(zone: "anz-1", locale: "{locale}")
    }
  }
}
`

// The preset accepts only SEEK AU/NZ job URLs and a public advertiser binding.
// Publishers cannot supply a GraphQL endpoint, query, headers or credentials.
func SeekDetailOptionsForSource(source string, config map[string]any) (SeekDetailOptions, error) {
	o := SeekDetailOptions{}
	u, err := url.Parse(source)
	if err != nil || len(source) > 8192 || !strings.EqualFold(u.Scheme, "https") || u.User != nil || u.Opaque != "" || strings.Contains(u.RawPath, "%") || u.Port() != "" && u.Port() != "443" {
		return o, ErrOptions
	}
	market, ok := seekMarkets[strings.ToLower(u.Hostname())]
	match := seekJobPath.FindStringSubmatch(u.Path)
	if !ok || match == nil {
		return o, ErrOptions
	}
	for key := range config {
		if key != "advertiser_id" {
			return o, ErrOptions
		}
	}
	if value, exists := config["advertiser_id"]; exists {
		var valid bool
		o.AdvertiserID, valid = value.(string)
		if !valid || !seekID.MatchString(o.AdvertiserID) {
			return o, ErrOptions
		}
	}
	o.JobID = match[1]
	locale := "en-AU"
	if market[1] == "nz.seek.com" {
		locale = "en-NZ"
	}
	query := strings.ReplaceAll(seekDetailQuery, "{locale}", locale)
	body, err := json.Marshal(map[string]any{"operationName": "JobDetails", "variables": map[string]any{"id": o.JobID}, "query": query})
	if err != nil {
		return o, err
	}
	o.Request = Request{Method: "POST", URL: "https://" + market[1] + "/graphql", Body: string(body), Headers: http.Header{"Content-Type": {"application/json"}, "Referer": {"https://" + market[1] + "/"}}}
	o.Path = "data.jobDetails.job"
	o.Fields = map[string]any{"title": "title", "description": "content", "locations": "location.label", "employment_type": "workTypes.label", "date_posted": "createdAt.dateTimeUtc", "valid_through": "expiresAt.dateTimeUtc", "metadata.seek_id": "id", "metadata.advertiser": "advertiser.name", "metadata.advertiser_id": "advertiser.id", "metadata.abstract": "abstract", "metadata.status": "status", "metadata.is_expired": "isExpired"}
	return o, nil
}

func ProjectSeekDetail(d *Document, o SeekDetailOptions) (map[string]any, error) {
	if d == nil {
		return nil, ErrInventory
	}
	values, err := ProjectHTTPDetail(d, o.HTTPDetailOptions)
	if err != nil || len(values) == 0 {
		return values, err
	}
	metadata, ok := values["metadata"].(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	id, err := d.String(metadata["seek_id"])
	if err != nil || id != o.JobID {
		return nil, ErrInventory
	}
	expired, valid := metadata["is_expired"].(bool)
	if !valid {
		text, stringValue := metadata["is_expired"].(string)
		valid, expired = stringValue && (text == "True" || text == "False"), text == "True"
	}
	status, statusValid := metadata["status"].(string)
	if !valid || !statusValid {
		return nil, ErrInventory
	}
	if expired || !strings.EqualFold(status, "active") {
		return map[string]any{}, nil
	}
	advertiser, err := d.String(metadata["advertiser_id"])
	if err != nil || !seekID.MatchString(advertiser) || o.AdvertiserID != "" && advertiser != o.AdvertiserID {
		return nil, ErrInventory
	}
	title, titleValid := values["title"].(string)
	description, descriptionValid := values["description"].(string)
	if !titleValid || !descriptionValid || strings.TrimSpace(title) == "" || strings.TrimSpace(description) == "" {
		return nil, ErrInventory
	}
	return values, nil
}
