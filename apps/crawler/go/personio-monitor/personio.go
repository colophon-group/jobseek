package personio

import (
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"regexp"
	"strings"
)

const MaxJobs = 50_000

var slugPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

type Job struct {
	URL            string         `json:"url"`
	Title          *string        `json:"title,omitempty"`
	Description    *string        `json:"description,omitempty"`
	Locations      []string       `json:"locations,omitempty"`
	EmploymentType *string        `json:"employment_type,omitempty"`
	DatePosted     *string        `json:"date_posted,omitempty"`
	Language       *string        `json:"language,omitempty"`
	Localizations  map[string]any `json:"localizations,omitempty"`
	Metadata       map[string]any `json:"metadata,omitempty"`
}

type section struct {
	Name  []string `xml:"name"`
	Value []string `xml:"value"`
}

type position struct {
	ID                 []string  `xml:"id"`
	Name               []string  `xml:"name"`
	Office             []string  `xml:"office"`
	EmploymentType     []string  `xml:"employmentType"`
	Schedule           []string  `xml:"schedule"`
	CreatedAt          []string  `xml:"createdAt"`
	Department         []string  `xml:"department"`
	Subcompany         []string  `xml:"subcompany"`
	RecruitingCategory []string  `xml:"recruitingCategory"`
	Seniority          []string  `xml:"seniority"`
	YearsOfExperience  []string  `xml:"yearsOfExperience"`
	Occupation         []string  `xml:"occupation"`
	OccupationCategory []string  `xml:"occupationCategory"`
	Keywords           []string  `xml:"keywords"`
	Sections           []section `xml:"jobDescriptions>jobDescription"`
}

func first(values []string) *string {
	if len(values) == 0 {
		return nil
	}
	value := strings.TrimSpace(values[0])
	if value == "" {
		return nil
	}
	return &value
}

func description(sections []section) *string {
	parts := []string{}
	for _, section := range sections {
		value := first(section.Value)
		if value == nil {
			continue
		}
		if name := first(section.Name); name != nil {
			parts = append(parts, "<h3>"+*name+"</h3>")
		}
		parts = append(parts, *value)
	}
	if len(parts) == 0 {
		return nil
	}
	combined := strings.Join(parts, "\n")
	return &combined
}

func employmentType(raw position) *string {
	if specific := first(raw.EmploymentType); specific != nil {
		switch strings.ToLower(*specific) {
		case "intern", "trainee", "freelance":
			value := strings.ToLower(*specific)
			return &value
		}
	}
	if schedule := first(raw.Schedule); schedule != nil {
		value := strings.ToLower(*schedule)
		return &value
	}
	return nil
}

func parsePosition(raw position, slug, domain string) (Job, bool) {
	id := first(raw.ID)
	if id == nil {
		return Job{}, false
	}
	metadata := map[string]any{"id": *id}
	for _, field := range []struct {
		name   string
		values []string
	}{
		{"department", raw.Department}, {"subcompany", raw.Subcompany},
		{"recruitingCategory", raw.RecruitingCategory}, {"seniority", raw.Seniority},
		{"yearsOfExperience", raw.YearsOfExperience}, {"occupation", raw.Occupation},
		{"occupationCategory", raw.OccupationCategory}, {"keywords", raw.Keywords},
	} {
		if value := first(field.values); value != nil {
			metadata[field.name] = *value
		}
	}
	var locations []string
	if office := first(raw.Office); office != nil {
		locations = []string{*office}
	}
	return Job{
		URL:   "https://" + slug + ".jobs.personio." + domain + "/job/" + *id,
		Title: first(raw.Name), Description: description(raw.Sections),
		Locations: locations, EmploymentType: employmentType(raw),
		DatePosted: first(raw.CreatedAt), Metadata: metadata,
	}, true
}

// ParseReader accepts the actual XML response consumed by the Python monitor.
// The callback can stream parsed rich jobs without keeping the XML tree in RAM.
func ParseReader(reader io.Reader, slug, domain string, emit func(Job) error) (positions int, jobs int, err error) {
	if !slugPattern.MatchString(slug) || domain != "de" && domain != "com" {
		return 0, 0, errors.New("invalid Personio slug or domain")
	}
	decoder := xml.NewDecoder(reader)
	for {
		token, decodeErr := decoder.Token()
		if decodeErr == io.EOF {
			return positions, jobs, nil
		}
		if decodeErr != nil {
			return positions, jobs, fmt.Errorf("parse Personio XML: %w", decodeErr)
		}
		start, ok := token.(xml.StartElement)
		if !ok || start.Name.Local != "position" {
			continue
		}
		var raw position
		if decodeErr := decoder.DecodeElement(&raw, &start); decodeErr != nil {
			return positions, jobs, fmt.Errorf("parse Personio position: %w", decodeErr)
		}
		positions++
		if parsed, valid := parsePosition(raw, slug, domain); valid {
			if err := emit(parsed); err != nil {
				return positions, jobs, err
			}
			jobs++
		}
	}
}
