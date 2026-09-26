package teamtailorrss

import (
	"bytes"
	"encoding/xml"
	"errors"
	"fmt"
	"html"
	"io"
	"strings"
)

const MaxJobs = 50_000
const PageSize = 100

type Job struct {
	URL             string         `json:"url"`
	Title           *string        `json:"title,omitempty"`
	Description     *string        `json:"description,omitempty"`
	Locations       []string       `json:"locations,omitempty"`
	JobLocationType *string        `json:"job_location_type,omitempty"`
	DatePosted      *string        `json:"date_posted,omitempty"`
	Metadata        map[string]any `json:"metadata,omitempty"`
}

type Inventory struct {
	Jobs      []Job `json:"jobs"`
	Truncated bool  `json:"truncated"`
}

type location struct {
	Name    string `xml:"https://teamtailor.com/locations name"`
	City    string `xml:"https://teamtailor.com/locations city"`
	Country string `xml:"https://teamtailor.com/locations country"`
}

type locationContainer struct {
	Locations []location `xml:"https://teamtailor.com/locations location"`
}

type item struct {
	Link         string            `xml:"link"`
	Title        string            `xml:"title"`
	Description  string            `xml:"description"`
	PubDate      string            `xml:"pubDate"`
	Guid         string            `xml:"guid"`
	RemoteStatus string            `xml:"remoteStatus"`
	Locations    locationContainer `xml:"https://teamtailor.com/locations locations"`
	Department   string            `xml:"https://teamtailor.com/locations department"`
	Role         string            `xml:"https://teamtailor.com/locations role"`
}

func optional(value string) *string {
	value = strings.TrimSpace(value)
	if value == "" {
		return nil
	}
	return &value
}

func parseItem(value item) (Job, bool) {
	link := optional(value.Link)
	if link == nil {
		return Job{}, false
	}
	var description *string
	if raw := optional(value.Description); raw != nil {
		decoded := html.UnescapeString(*raw)
		description = &decoded
	}
	locations := make([]string, 0, len(value.Locations.Locations))
	for _, place := range value.Locations.Locations {
		if name := strings.TrimSpace(place.Name); name != "" {
			locations = append(locations, name)
			continue
		}
		city, country := strings.TrimSpace(place.City), strings.TrimSpace(place.Country)
		if city != "" && country != "" {
			locations = append(locations, city+", "+country)
		} else if city != "" {
			locations = append(locations, city)
		} else if country != "" {
			locations = append(locations, country)
		}
	}
	var locationType *string
	if status := strings.ToLower(strings.TrimSpace(value.RemoteStatus)); status != "" {
		switch {
		case strings.Contains(status, "fully") || status == "remote":
			locationType = optional("remote")
		case strings.Contains(status, "hybrid"):
			locationType = optional("hybrid")
		case status == "none" || status == "onsite" || status == "on-site":
			locationType = optional("onsite")
		}
	}
	if len(locations) == 0 && locationType != nil && *locationType == "remote" {
		locations = append(locations, "Remote")
	}
	metadata := map[string]any{}
	for _, field := range []struct{ name, value string }{
		{"id", value.Guid}, {"department", value.Department}, {"role", value.Role},
	} {
		if text := strings.TrimSpace(field.value); text != "" {
			metadata[field.name] = text
		}
	}
	if len(metadata) == 0 {
		metadata = nil
	}
	return Job{
		URL: *link, Title: optional(value.Title), Description: description,
		Locations: locations, JobLocationType: locationType,
		DatePosted: optional(value.PubDate), Metadata: metadata,
	}, true
}

// ParsePage reads one complete RSS page and returns both the number of raw
// items and the rich jobs. Pagination uses the raw item count, as Python does.
func ParsePage(body []byte) ([]Job, int, error) {
	body = bytes.TrimPrefix(body, []byte{0xef, 0xbb, 0xbf})
	head := bytes.ToLower(bytes.TrimSpace(body))
	if !(bytes.HasPrefix(head, []byte("<?xml")) || bytes.HasPrefix(head, []byte("<rss")) || bytes.HasPrefix(head, []byte("<feed"))) {
		return nil, 0, errors.New("Teamtailor feed returned non-XML content")
	}
	decoder := xml.NewDecoder(bytes.NewReader(body))
	jobs := []Job{}
	items := 0
	for {
		token, err := decoder.Token()
		if err == io.EOF {
			return jobs, items, nil
		}
		if err != nil {
			return nil, 0, fmt.Errorf("parse Teamtailor RSS: %w", err)
		}
		start, ok := token.(xml.StartElement)
		if !ok || start.Name.Local != "item" {
			continue
		}
		var raw item
		if err := decoder.DecodeElement(&raw, &start); err != nil {
			return nil, 0, fmt.Errorf("parse Teamtailor item: %w", err)
		}
		items++
		if parsed, valid := parseItem(raw); valid {
			jobs = append(jobs, parsed)
		}
	}
}
