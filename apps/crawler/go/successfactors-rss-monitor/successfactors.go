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
	"unicode"

	"golang.org/x/text/cases"
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
	Link           []string `xml:"link"`
	Title          []string `xml:"title"`
	Description    []string `xml:"description"`
	GUID           []string `xml:"guid"`
	PubDate        []string `xml:"pubDate"`
	Location       []string `xml:"http://base.google.com/ns/1.0 location"`
	ExpirationDate []string `xml:"http://base.google.com/ns/1.0 expiration_date"`
	Employer       []string `xml:"http://base.google.com/ns/1.0 employer"`
	JobFunction    []string `xml:"http://base.google.com/ns/1.0 job_function"`
}

// ElementTree's .text stops at the first child element. Match namespaces
// exactly and retain the first repeated field even when it is empty.
func (value *item) UnmarshalXML(d *xml.Decoder, start xml.StartElement) error {
	for {
		token, err := d.Token()
		if err != nil {
			return err
		}
		switch token := token.(type) {
		case xml.StartElement:
			var target *[]string
			if token.Name.Space == "" {
				switch token.Name.Local {
				case "link":
					target = &value.Link
				case "title":
					target = &value.Title
				case "description":
					target = &value.Description
				case "guid":
					target = &value.GUID
				case "pubDate":
					target = &value.PubDate
				}
			} else if token.Name.Space == "http://base.google.com/ns/1.0" {
				switch token.Name.Local {
				case "location":
					target = &value.Location
				case "expiration_date":
					target = &value.ExpirationDate
				case "employer":
					target = &value.Employer
				case "job_function":
					target = &value.JobFunction
				}
			}
			if target == nil {
				if err := d.Skip(); err != nil {
					return err
				}
				continue
			}
			var text firstText
			if err := d.DecodeElement(&text, &token); err != nil {
				return err
			}
			*target = append(*target, string(text))
		case xml.EndElement:
			return nil
		}
	}
}

type firstText string

func (text *firstText) UnmarshalXML(d *xml.Decoder, start xml.StartElement) error {
	var out strings.Builder
	beforeChild := true
	for {
		token, err := d.Token()
		if err != nil {
			return err
		}
		switch token := token.(type) {
		case xml.CharData:
			if beforeChild {
				out.Write(token)
			}
		case xml.StartElement:
			beforeChild = false
			if err := d.Skip(); err != nil {
				return err
			}
		case xml.EndElement:
			*text = firstText(out.String())
			return nil
		}
	}
}

const pythonSpacePattern = `[\p{Z}\t\n\f\r\v\x{0085}\x{001c}-\x{001f}]`

func pythonSpace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
func pythonPattern(pattern string) *regexp.Regexp {
	return regexp.MustCompile(strings.ReplaceAll(pattern, `\s`, pythonSpacePattern))
}

var (
	htmlTag             = regexp.MustCompile(`<[^>]+>`)
	descriptionLocation = pythonPattern(`(?i)<(?:strong|b)\b[^>]*>\s*Location\s*:?\s*</(?:strong|b)>\s*([^<]+)`)
	titleLocationSuffix = pythonPattern(`\s*\([^)]+,\s*[^)]+\)\s*$`)
	titleLocationValue  = pythonPattern(`\s*\(([^()]+,\s*[^()]+)\)\s*$`)
)

func optional(value string) *string {
	if value == "" {
		return nil
	}
	value = strings.TrimFunc(value, pythonSpace)
	return &value
}

func normalizedText(value string) string {
	return strings.Join(strings.FieldsFunc(value, pythonSpace), " ")
}

func first(values []string) string {
	if len(values) == 0 {
		return ""
	}
	return values[0]
}

func parseItem(value item) (Job, bool) {
	link := optional(first(value.Link))
	if link == nil || *link == "" {
		return Job{}, false
	}
	title := optional(first(value.Title))
	var description *string
	if raw := optional(first(value.Description)); raw != nil && *raw != "" {
		decoded := html.UnescapeString(*raw)
		description = &decoded
	}
	if description != nil && *description != "" && title != nil && *title != "" {
		plainDescription := cases.Fold().String(normalizedText(htmlTag.ReplaceAllString(*description, " ")))
		plainTitle := cases.Fold().String(normalizedText(html.UnescapeString(*title)))
		if plainDescription == plainTitle {
			description = nil
		}
	}
	location := optional(first(value.Location))
	stripTitleLocation := location != nil
	if (location == nil || *location == "") && description != nil && *description != "" {
		if match := descriptionLocation.FindStringSubmatch(*description); len(match) == 2 {
			location = optional(normalizedText(html.UnescapeString(match[1])))
			if location != nil && title != nil {
				if match := titleLocationValue.FindStringSubmatch(*title); len(match) == 2 {
					candidate := normalizedText(match[1])
					descriptionKey, candidateKey := cases.Fold().String(*location), cases.Fold().String(candidate)
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
	if location != nil && *location != "" {
		locations = []string{*location}
	}
	metadata := map[string]any{}
	for _, field := range []struct{ name, value string }{
		{"id", first(value.GUID)}, {"employer", first(value.Employer)},
		{"expiration_date", first(value.ExpirationDate)},
	} {
		if text := optional(field.value); text != nil && *text != "" {
			metadata[field.name] = *text
		}
	}
	if function := optional(first(value.JobFunction)); function != nil && *function != "" && *function != "ATS_WEBFORM" {
		metadata["job_function"] = *function
	}
	if len(metadata) == 0 {
		metadata = nil
	}
	return Job{
		URL: *link, Title: title, Description: description,
		Locations: locations, DatePosted: optional(first(value.PubDate)), Metadata: metadata,
	}, true
}

// Bound an individual XML token/item, not a whole streamed inventory. Large
// boards legitimately exceed the pilot's former 256 MiB aggregate response cap.
const maxXMLReadWindow = 32 << 20

type xmlReadWindow struct {
	source    io.Reader
	remaining int
}

func (r *xmlReadWindow) Read(p []byte) (int, error) {
	if r.remaining == 0 {
		return 0, errors.New("SuccessFactors XML item/token exceeded 32 MiB")
	}
	if len(p) > r.remaining {
		p = p[:r.remaining]
	}
	n, err := r.source.Read(p)
	r.remaining -= n
	return n, err
}

// ParseStream sends each parsed job to emit without retaining the feed or its
// jobs. The caller must treat an error after emitted jobs as a failed cycle.
func ParseStream(reader io.Reader, emit func(Job) error) (items int, jobs int, truncated bool, err error) {
	window := &xmlReadWindow{source: reader, remaining: maxXMLReadWindow}
	decoder := xml.NewDecoder(window)
	for {
		window.remaining = maxXMLReadWindow
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
