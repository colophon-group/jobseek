package worker

import (
	"errors"
	"html"
	"net/url"
	"regexp"
	"strings"

	htmlnode "golang.org/x/net/html"
)

const governmentJobsNamespace = "http://www.neogov.com/namespaces/JobListing"

var zohoRecruitHost = regexp.MustCompile(`^([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.zohorecruit\.(com|ca|eu|in|com\.cn|com\.au|jp|uk|sa)$`)
var zohoRecruitLocation = regexp.MustCompile(`(?is)(?:Lieu|Location)\s*:\s*(.*?)\s*<br\s*/?>`)
var rssASCIIID = regexp.MustCompile(`^[0-9]+$`)

func rssProviderJob(fields map[string]string, preset string) (RichMonitorJob, error) {
	optional := func(s string) *string {
		if s == "" {
			return nil
		}
		return &s
	}
	job := RichMonitorJob{URL: fields["link"], Title: optional(fields["title"]), Description: optional(html.UnescapeString(fields["description"])), DatePosted: optional(fields["pubDate"])}
	location := fields["location"]
	if location == "" {
		location = fields["Location"]
	}
	if location != "" {
		job.Locations = []string{location}
	}
	id := fields["guid"]
	if id == "" {
		id = fields["JobID"]
	}
	if id != "" {
		job.Metadata = map[string]any{"id": id}
	}
	switch preset {
	case "generic":
	case "governmentjobs":
		field := func(name string) string { return fields[governmentJobsNamespace+"\x00"+name] }
		sections := []struct{ heading, value string }{{"", fields["description"]}, {"Examples of Duties", field("examplesofduties")}, {"Qualifications", field("qualifications")}, {"Supplemental Information", field("supplementalinformation")}}
		parts := []string{}
		for _, section := range sections {
			if section.value == "" {
				continue
			}
			if section.heading != "" {
				parts = append(parts, "<h2>"+section.heading+"</h2>")
			}
			parts = append(parts, html.UnescapeString(section.value))
		}
		job.Description = optional(strings.Join(parts, "\n"))
		job.Locations = nil
		if location := field("location"); location != "" {
			job.Locations = []string{location}
		}
		job.EmploymentType = nil
		if employment := field("jobType"); employment != "" {
			job.EmploymentType = employment
		}
		job.Metadata = nil
		for _, entry := range []struct{ key, value string }{{"id", field("jobId")}, {"department", field("department")}} {
			if entry.value != "" {
				if job.Metadata == nil {
					job.Metadata = map[string]any{}
				}
				job.Metadata[entry.key] = entry.value
			}
		}
	case "zoho_recruit":
		if job.Title != nil {
			job.Title = optional(html.UnescapeString(*job.Title))
		}
		job.Locations = nil
		if job.Description != nil {
			if match := zohoRecruitLocation.FindStringSubmatch(*job.Description); match != nil {
				doc, err := htmlnode.Parse(strings.NewReader(html.UnescapeString(match[1])))
				if err != nil {
					return RichMonitorJob{}, err
				}
				var text strings.Builder
				var walk func(*htmlnode.Node)
				walk = func(n *htmlnode.Node) {
					if n.Type == htmlnode.TextNode {
						text.WriteString(n.Data)
					}
					for child := n.FirstChild; child != nil; child = child.NextSibling {
						walk(child)
					}
				}
				walk(doc)
				if location := strings.Join(strings.Fields(text.String()), " "); location != "" {
					job.Locations = []string{location}
				}
			}
		}
		job.Metadata = nil
		if guid := fields["guid"]; guid != "" {
			job.Metadata = map[string]any{"id": guid}
			if u, err := url.Parse(job.URL); err == nil && rssASCIIID.MatchString(guid) {
				if tenant := zohoRecruitHost.FindStringSubmatch(strings.TrimRight(strings.ToLower(u.Hostname()), ".")); tenant != nil {
					job.SourceIdentity = "zoho_recruit:" + tenant[1] + "." + tenant[2] + ":" + guid
				}
			}
		}
	default:
		return RichMonitorJob{}, errors.New("unsupported RSS item preset")
	}
	return job, nil
}
