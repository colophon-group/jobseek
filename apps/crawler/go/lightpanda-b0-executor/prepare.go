package executor

import (
	"context"
	"encoding/json"
	"errors"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

var ErrEmptyResult = errors.New("scrape has no usable required content")

type LocationLookup interface {
	Resolve(context.Context, []string, string, string) ([]int64, []string, error)
}

type EnrichSnapshot struct {
	Titles         []string
	LocationIDs    []int64
	EmploymentType *string
}

type PreparedContent struct {
	Fields      ContentFields
	Description *DescriptionCandidate
	Enrich      bool
}

type Processor struct {
	Matcher   *enrichment.Matcher
	Lookups   *NativeLookups
	Locations LocationLookup
}

func optionalText(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

func selectedEnrichment(config map[string]any) (map[string]bool, bool) {
	values, ok := config["enrich"].([]any)
	if !ok || len(values) == 0 {
		return nil, false
	}
	fields := map[string]bool{}
	for _, value := range values {
		if name, ok := value.(string); ok {
			fields[name] = true
		}
	}
	return fields, true
}

// Prepare preserves the ordinary/detail and selected-enrichment paths before
// one fenced persistence transaction. Missing columns stay SQL NULL. Location
// backfill is the sole database read in this assembly and uses its existing pool.
func (p *Processor) Prepare(ctx context.Context, content, config map[string]any, existing *EnrichSnapshot) (*PreparedContent, error) {
	if p == nil || p.Matcher == nil || p.Lookups == nil || p.Locations == nil {
		return nil, errors.New("native enrichment unavailable")
	}
	fields, enrich := selectedEnrichment(config)
	if !enrich {
		title, ok := content["title"].(string)
		// Python's step-zero garbage check runs before scalar coercion. A
		// non-string title cannot pass its .strip() call or missing-title gate.
		if !ok || title == "" || GarbageTitle(title) {
			return nil, ErrEmptyResult
		}
	}
	var description *string
	if content["description"] != nil {
		raw, ok := content["description"].(string)
		if !ok {
			return nil, errors.New("description is not text")
		}
		var err error
		description, err = enrichment.NormalizeDescriptionHTML(raw)
		if err != nil {
			return nil, err
		}
	}
	if enrich {
		hasData := false
		for field := range fields {
			value := content[field]
			if field == "description" {
				value = nil
				if description != nil {
					value = *description
				}
			}
			if value != nil {
				hasData = true
			}
		}
		if !hasData {
			return nil, ErrEmptyResult
		}
		if existing != nil {
			title, err := CoerceText(content["title"])
			if err != nil {
				return nil, err
			}
			locations, err := CoerceLocations(content["locations"])
			if err != nil {
				return nil, err
			}
			emp, err := CoerceText(content["employment_type"])
			if err != nil {
				return nil, err
			}
			if len(existing.Titles) == 0 && title != nil && !GarbageTitle(*title) {
				fields["title"] = true
			}
			if len(existing.LocationIDs) == 0 && len(locations) > 0 {
				fields["locations"] = true
			}
			if optionalText(existing.EmploymentType) == "" && emp != nil {
				fields["employment_type"] = true
			}
		}
	}
	var language *string
	var err error
	if !pythonTruth(content["language"]) && description != nil {
		result, failure := p.Matcher.Process(enrichment.Request{Operation: "language", Description: *description})
		if failure != nil {
			return nil, failure
		}
		language = result.Language
	} else {
		language, err = CoerceText(content["language"])
		if err != nil {
			return nil, err
		}
	}
	var detected []string
	if description != nil && (!enrich || fields["title"]) {
		result, err := p.Matcher.Process(enrichment.Request{Operation: "all_languages", Description: *description})
		if err != nil {
			return nil, err
		}
		detected = result.Languages
	}
	prepared := &PreparedContent{Enrich: enrich}
	var rawEmployment *string
	if !enrich || fields["employment_type"] {
		rawEmployment, err = CoerceText(content["employment_type"])
		if err != nil {
			return nil, err
		}
		prepared.Fields.EmploymentType = EmploymentType(rawEmployment)
	}
	if !enrich || fields["title"] {
		title, err := CoerceText(content["title"])
		if err != nil {
			return nil, err
		}
		prepared.Fields.Titles = BuildTitles(title)
		var matchingTitles []string
		if enrich {
			matchingTitles = prepared.Fields.Titles
		} else if title != nil {
			matchingTitles = []string{*title}
		}
		prepared.Fields.OccupationID, prepared.Fields.SeniorityID, err = p.Lookups.ResolveTitles(p.Matcher, matchingTitles, optionalText(rawEmployment))
		if err != nil {
			return nil, err
		}
		if language != nil || len(detected) > 0 {
			prepared.Fields.Locales = BuildLocales(language, detected)
		}
	} else if fields["employment_type"] {
		_, prepared.Fields.SeniorityID, err = p.Lookups.ResolveTitles(p.Matcher, nil, optionalText(rawEmployment))
		if err != nil {
			return nil, err
		}
	}
	if !enrich || fields["locations"] {
		locations, err := CoerceLocations(content["locations"])
		if err != nil {
			return nil, err
		}
		kind, err := CoerceText(content["job_location_type"])
		if err != nil {
			return nil, err
		}
		prepared.Fields.LocationIDs, prepared.Fields.LocationTypes, err = p.Locations.Resolve(ctx, locations, optionalText(kind), optionalText(language))
		if err != nil {
			return nil, err
		}
	}
	if !enrich || fields["description"] {
		derived, err := p.Lookups.DescriptionFields(p.Matcher, optionalText(description))
		if err != nil {
			return nil, err
		}
		prepared.Fields.TechnologyIDs = derived.TechnologyIDs
		prepared.Fields.SalaryMin, prepared.Fields.SalaryMax = derived.SalaryMin, derived.SalaryMax
		prepared.Fields.SalaryCurrency, prepared.Fields.SalaryPeriod = derived.SalaryCurrency, derived.SalaryPeriod
		prepared.Fields.SalaryEUR = derived.SalaryEUR
		prepared.Fields.ExperienceMin, prepared.Fields.ExperienceMax = derived.ExperienceMin, derived.ExperienceMax
		prepared.Description, err = StageDescription(optionalText(description), optionalText(language))
		if err != nil {
			return nil, err
		}
		if enrich && prepared.Description != nil {
			result, err := p.Matcher.Process(enrichment.Request{Operation: "all_languages", Description: prepared.Description.HTML})
			if err != nil {
				return nil, err
			}
			prepared.Fields.Locales = BuildLocales(&prepared.Description.Locale, result.Languages)
		}
	}
	return prepared, nil
}

func pythonTruth(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case []any:
		return len(v) > 0
	case []string:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	case json.Number:
		n, err := v.Float64()
		return err != nil || n != 0
	case float64:
		return v != 0
	case int:
		return v != 0
	case int64:
		return v != 0
	default:
		return true
	}
}
