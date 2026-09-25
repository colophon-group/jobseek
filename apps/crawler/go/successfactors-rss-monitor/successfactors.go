package successfactorsrss

import (
	"bytes"
	"encoding/xml"
	"errors"
	"fmt"
	"html"
	"io"
	"regexp"
	"strings"
)

const MaxJobs = 50_000

type Job struct {
	URL         string         `json:"url"`
	Title       *string        `json:"title,omitempty"`
	Description *string        `json:"description,omitempty"`
	Locations   []string       `json:"locations,omitempty"`
	DatePosted  *string        `json:"date_posted,omitempty"`
	Metadata    map[string]any `json:"metadata,omitempty"`
}

type item struct {
	Link           string `xml:"link"`
	Title          string `xml:"title"`
	Description    string `xml:"description"`
	GUID           string `xml:"guid"`
	PubDate        string `xml:"pubDate"`
	Location       string `xml:"http://base.google.com/ns/1.0 location"`
	ExpirationDate string `xml:"http://base.google.com/ns/1.0 expiration_date"`
	Employer       string `xml:"http://base.google.com/ns/1.0 employer"`
	JobFunction    string `xml:"http://base.google.com/ns/1.0 job_function"`
}

var (
	htmlTag             = regexp.MustCompile(`<[^>]+>`)
	descriptionLocation = regexp.MustCompile(`(?i)<(?:strong|b)\b[^>]*>\s*Location\s*:?\s*</(?:strong|b)>\s*([^<]+)`)
	titleLocationSuffix = regexp.MustCompile(`\s*\([^)]+,\s*[^)]+\)\s*$`)
	titleLocationValue  = regexp.MustCompile(`\s*\(([^()]+,\s*[^()]+)\)\s*$`)
)

func optional(value string) *string {
	value = strings.TrimSpace(value)
	if value == "" {
		return nil
	}
	return &value
}

func normalizedText(value string) string {
	return strings.Join(strings.Fields(value), " ")
}

func parseItem(value item) (Job, bool) {
	link := optional(value.Link)
	if link == nil {
		return Job{}, false
	}
	title := optional(value.Title)
	var description *string
	if raw := optional(value.Description); raw != nil {
		description = optional(html.UnescapeString(*raw))
	}
	if description != nil && title != nil {
		plainDescription := strings.ToLower(normalizedText(htmlTag.ReplaceAllString(*description, " ")))
		plainTitle := strings.ToLower(normalizedText(html.UnescapeString(*title)))
		if plainDescription == plainTitle {
			description = nil
		}
	}
	location := optional(value.Location)
	stripTitleLocation := location != nil
	if location == nil && description != nil {
		if match := descriptionLocation.FindStringSubmatch(*description); len(match) == 2 {
			location = optional(normalizedText(html.UnescapeString(match[1])))
			if location != nil && title != nil {
				if match := titleLocationValue.FindStringSubmatch(*title); len(match) == 2 {
					candidate := normalizedText(match[1])
					descriptionKey, candidateKey := strings.ToLower(*location), strings.ToLower(candidate)
					if candidateKey == descriptionKey || strings.HasPrefix(candidateKey, descriptionKey+",") {
						location = &candidate
						stripTitleLocation = true
					}
				}
			}
		}
	}
	if title != nil && stripTitleLocation {
		if cleaned := titleLocationSuffix.ReplaceAllString(*title, ""); cleaned != "" {
			title = &cleaned
		}
	}
	var locations []string
	if location != nil {
		locations = []string{*location}
	}
	metadata := map[string]any{}
	for _, field := range []struct{ name, value string }{
		{"id", value.GUID}, {"employer", value.Employer},
		{"expiration_date", value.ExpirationDate},
	} {
		if text := optional(field.value); text != nil {
			metadata[field.name] = *text
		}
	}
	if function := optional(value.JobFunction); function != nil && *function != "ATS_WEBFORM" {
		metadata["job_function"] = *function
	}
	if len(metadata) == 0 {
		metadata = nil
	}
	return Job{
		URL: *link, Title: title, Description: description,
		Locations: locations, DatePosted: optional(value.PubDate), Metadata: metadata,
	}, true
}

// ParseStream sends each parsed job to emit without retaining the feed or its
// jobs. The caller must treat an error after emitted jobs as a failed cycle.
func ParseStream(reader io.Reader, emit func(Job) error) (items int, jobs int, truncated bool, err error) {
	decoder := xml.NewDecoder(reader)
	for {
		token, decodeErr := decoder.Token()
		if decodeErr == io.EOF {
			return items, jobs, false, nil
		}
		if decodeErr != nil {
			return items, jobs, false, fmt.Errorf("parse SuccessFactors RSS: %w", decodeErr)
		}
		start, ok := token.(xml.StartElement)
		if !ok || start.Name.Local != "item" {
			continue
		}
		var raw item
		if decodeErr := decoder.DecodeElement(&raw, &start); decodeErr != nil {
			return items, jobs, false, fmt.Errorf("parse SuccessFactors item: %w", decodeErr)
		}
		items++
		if job, valid := parseItem(raw); valid {
			if emitErr := emit(job); emitErr != nil {
				return items, jobs, false, emitErr
			}
			jobs++
			if jobs == MaxJobs {
				return items, jobs, true, nil
			}
		}
	}
}

// ParseReader reads a complete Google Base RSS document and returns raw item
// count separately from the rich jobs, matching the Python monitor's cap.
func ParseReader(reader io.Reader) ([]Job, int, error) {
	collected := []Job{}
	items, _, _, err := ParseStream(reader, func(job Job) error {
		collected = append(collected, job)
		return nil
	})
	if err != nil {
		return nil, 0, err
	}
	return collected, items, nil
}

func ParsePage(body []byte) ([]Job, int, error) {
	body = bytes.TrimPrefix(body, []byte{0xef, 0xbb, 0xbf})
	head := bytes.ToLower(bytes.TrimSpace(body))
	if !(bytes.HasPrefix(head, []byte("<?xml")) || bytes.HasPrefix(head, []byte("<rss")) || bytes.HasPrefix(head, []byte("<feed"))) {
		return nil, 0, errors.New("SuccessFactors feed returned non-XML content")
	}
	return ParseReader(bytes.NewReader(body))
}
