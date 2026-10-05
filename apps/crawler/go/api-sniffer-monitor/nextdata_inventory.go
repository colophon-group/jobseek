package apisniffer

import (
	"encoding/json"
	"regexp"
	"strings"

	"golang.org/x/text/unicode/norm"
)

var nextdataSlugSeparators = regexp.MustCompile(`[^a-z0-9]+`)

func nextdataSlug(value string) string {
	var ascii strings.Builder
	for _, r := range norm.NFKD.String(value) {
		if r < 128 {
			ascii.WriteRune(r)
		}
	}
	value = strings.ReplaceAll(strings.ReplaceAll(strings.ToLower(ascii.String()), "&", "and"), "+", "plus")
	return strings.Trim(nextdataSlugSeparators.ReplaceAllString(value, "-"), "-")
}

// ProjectNextdataItems keeps the actual monitor field/template semantics while
// sharing the existing precise scalar coercion and configured field extractor.
// Its caller proves page completeness and tenant filters before persistence.
// URL-only callers retain URLs separately; no rich field is inferred or dropped.
func (d *Document) ProjectNextdataItems(items []any, template string, slugFields []string, fields map[string]any) ([]Job, error) {
	return d.ProjectNextdataIdentityItems(items, template, slugFields, fields, nil)
}

func (d *Document) ProjectNextdataIdentityItems(items []any, template string, slugFields []string, fields map[string]any, identity *NextdataIdentity) ([]Job, error) {
	jobs := []Job{}
	if d == nil {
		return nil, ErrInventory
	}
	projection := *d
	// The Python monitor calls extract_field without a sibling lookup root.
	// The API/detail caller's default root must not leak into this contract.
	projection.Root = map[string]any{}
	for _, raw := range items {
		row, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		variables := map[string]string{}
		for key, value := range row {
			switch value.(type) {
			case string, json.Number, bool:
				text, err := d.String(value)
				if err != nil {
					return nil, err
				}
				variables[key] = text
			}
		}
		parts := []string{}
		for _, field := range slugFields {
			if value := row[field]; value != nil {
				text, err := d.String(value)
				if err != nil {
					return nil, err
				}
				parts = append(parts, nextdataSlug(text))
			}
		}
		if len(parts) > 0 {
			variables["slug"] = strings.Join(parts, "-")
		}
		source, err := formatTemplate(template, variables, true)
		if err != nil || source == "" {
			continue
		}
		job := Job{URL: source, Metadata: map[string]any{}}
		if identity != nil {
			v, err := Search(row, identity.Field)
			if err != nil {
				return nil, ErrInventory
			}
			switch v := v.(type) {
			case string:
				job.SourceIdentity = strings.TrimSpace(v)
			case json.Number:
				if strings.ContainsAny(string(v), ".eE") {
					return nil, ErrInventory
				}
				job.SourceIdentity = string(v)
			default:
				return nil, ErrInventory
			}
			job.SourceIdentity = identity.Provider + ":" + identity.Tenant + ":" + job.SourceIdentity
			if !identity.Valid(job.SourceIdentity) {
				return nil, ErrInventory
			}
		}
		for target, spec := range fields {
			value, err := projection.Field(row, spec)
			if err != nil {
				return nil, err
			}
			if value == nil {
				continue
			}
			switch target {
			case "title":
				job.Title = value
			case "description":
				job.Description = value
			case "employment_type":
				job.EmploymentType = value
			case "job_location_type":
				job.JobLocationType = value
			case "date_posted":
				job.DatePosted = value
			case "locations":
				switch value := value.(type) {
				case string:
					job.Locations = []string{value}
				case []string:
					job.Locations = value
				case []any:
					for _, element := range value {
						text, ok := element.(string)
						if !ok {
							return nil, ErrInventory
						}
						job.Locations = append(job.Locations, text)
					}
				default:
					return nil, ErrInventory
				}
			default:
				job.Metadata[strings.TrimPrefix(target, "metadata.")] = value
			}
		}
		if len(job.Metadata) == 0 {
			job.Metadata = nil
		}
		jobs = append(jobs, job)
	}
	return jobs, nil
}
