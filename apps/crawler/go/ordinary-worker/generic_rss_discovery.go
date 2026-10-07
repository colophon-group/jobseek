package worker

import (
	"bytes"
	"context"
	"encoding/xml"
	"errors"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"golang.org/x/net/html/charset"
	"io"
	"net/http"
	"strings"
)

// ElementTree's child.text means the text before a child's first nested tag;
// text after nested markup is not part of a feed field.
func genericFeedText(d *xml.Decoder, start xml.StartElement) (string, error) {
	var text strings.Builder
	depth := 1
	leading := true
	for depth > 0 {
		token, err := d.Token()
		if err != nil {
			return "", err
		}
		switch t := token.(type) {
		case xml.StartElement:
			depth++
			leading = false
		case xml.EndElement:
			depth--
		case xml.CharData:
			if depth == 1 && leading {
				text.Write([]byte(t))
			}
		}
	}
	return strings.TrimSpace(text.String()), nil
}

func parseGenericRSS(raw []byte) (RichDiscovery, error) {
	return parseRSSProvider(raw, "generic")
}

func parseRSSProvider(raw []byte, preset string) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	head := strings.TrimLeft(string(raw), "\ufeff \t\r\n")
	lower := strings.ToLower(head)
	if !strings.HasPrefix(lower, "<?xml") && !strings.HasPrefix(lower, "<rss") && !strings.HasPrefix(lower, "<feed") {
		return result, errors.New("RSS non-XML response")
	}
	d := xml.NewDecoder(bytes.NewReader(raw))
	d.CharsetReader = charset.NewReaderLabel
	depth := 0
	seenRoot := false
	for {
		token, err := d.Token()
		if err == io.EOF {
			if !seenRoot || depth != 0 {
				return RichDiscovery{}, errors.New("RSS incomplete XML")
			}
			return result, nil
		}
		if err != nil {
			return RichDiscovery{}, err
		}
		switch x := token.(type) {
		case xml.StartElement:
			if depth == 0 {
				if seenRoot {
					return RichDiscovery{}, errors.New("RSS multiple roots")
				}
				seenRoot = true
			}
			depth++
		case xml.EndElement:
			depth--
		case xml.CharData:
			if depth == 0 && strings.TrimSpace(string(x)) != "" {
				return RichDiscovery{}, errors.New("RSS data outside root")
			}
		}
		start, ok := token.(xml.StartElement)
		if !ok || start.Name.Local != "item" {
			continue
		}
		fields := map[string]string{}
		for {
			token, err = d.Token()
			if err != nil {
				return RichDiscovery{}, err
			}
			if end, ok := token.(xml.EndElement); ok && end.Name == start.Name {
				break
			}
			if child, ok := token.(xml.StartElement); ok {
				value, err := genericFeedText(d, child)
				if err != nil {
					return RichDiscovery{}, err
				}
				key := child.Name.Local
				if child.Name.Space != "" {
					key = child.Name.Space + "\x00" + key
				}
				if _, exists := fields[key]; !exists {
					fields[key] = value
				}
			}
		}
		depth--
		if fields["link"] == "" {
			if preset == "hr_manager" {
				return RichDiscovery{}, errHRManagerInventory
			}
			continue
		}
		job, err := rssProviderJob(fields, preset)
		if err != nil {
			return RichDiscovery{}, err
		}
		result.Jobs = append(result.Jobs, job)
		if len(result.Jobs) == 50000 {
			result.Truncated = true
			return result, nil
		}
	}
}

type genericFeedResource string

func (f genericFeedResource) ResourceMatches(raw string) bool { return raw == string(f) }

func discoverGenericRSS(ctx context.Context, client *http.Client, profile queue.GreenhouseMonitorProfile) (RichDiscovery, error) {
	result := RichDiscovery{Jobs: []RichMonitorJob{}}
	for attempt := 0; attempt < 3; attempt++ {
		body, observed, err := fetchProviderStatusResource(ctx, client, genericFeedResource(profile.Endpoint), profile.Endpoint, nil, nil, 32<<20, nil)
		result.Response = observed
		if err == nil {
			preset := "generic"
			if strings.HasPrefix(profile.Profile, "rss.hr_manager-") {
				preset = "hr_manager"
			}
			if strings.HasPrefix(profile.Profile, "rss.governmentjobs-") {
				preset = "governmentjobs"
			}
			if strings.HasPrefix(profile.Profile, "rss.zoho_recruit-") {
				preset = "zoho_recruit"
			}
			var parsed RichDiscovery
			var e error
			switch {
			case strings.HasPrefix(profile.Profile, "rss.generic-summary-"):
				parsed, e = parseGenericStructuredSummary(body)
			case strings.HasPrefix(profile.Profile, "rss.successfactors-legacy-xml-"):
				origin, company, err := queue.SuccessFactorsLegacyXMLIdentity(profile.Endpoint)
				if err != nil {
					return result, err
				}
				parsed, e = parseSFLegacyXML(body, origin, company)
			default:
				parsed, e = parseRSSProvider(body, preset)
			}
			parsed.Response = observed
			return parsed, e
		}
		if observed != nil && observed.reserved {
			return result, err
		}
		if ctx.Err() != nil {
			return result, ctx.Err()
		}
		if observed != nil && observed.status != 408 && observed.status != 425 && observed.status != 429 && observed.status < 500 {
			return result, err
		}
		if attempt == 2 {
			return result, err
		}
		if err = almaRetry(ctx, attempt); err != nil {
			return result, err
		}
	}
	return result, errors.New("RSS retry exhausted")
}
