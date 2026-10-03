package executor

import (
	"context"
	"errors"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

// RichMonitorContent is the complete content contract emitted by the standard
// token Greenhouse API monitor. Extras, localizations and detail enrichment
// require separate processing contracts before their profiles can be selected.
type RichMonitorContent struct {
	Title, Description *string
	Locations          []string
	Language           any
}

// PrepareRichMonitor matches board._build_rich_new_records for this contract.
// Unlike detail preparation, rich discovery accepts missing/garbage titles and
// always supplies locales, including an empty-content inventory's default en.
// Fetching and enrichment happen outside the caller's authoritative transaction.
func (p *Processor) PrepareRichMonitor(ctx context.Context, content RichMonitorContent) (*PreparedContent, error) {
	if p == nil || p.Matcher == nil || p.Lookups == nil || p.Locations == nil {
		return nil, errors.New("native enrichment unavailable")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	var description *string
	var err error
	if content.Description != nil {
		description, err = enrichment.NormalizeDescriptionHTML(*content.Description)
		if err != nil {
			return nil, err
		}
	}
	var language *string
	if !pythonTruth(content.Language) && description != nil && *description != "" {
		result, failure := p.Matcher.Process(enrichment.Request{Operation: "language", Description: *description})
		if failure != nil {
			return nil, failure
		}
		language = result.Language
	} else {
		language, err = CoerceText(content.Language)
		if err != nil {
			return nil, err
		}
	}
	var detected []string
	if description != nil && *description != "" {
		result, failure := p.Matcher.Process(enrichment.Request{Operation: "all_languages", Description: *description})
		if failure != nil {
			return nil, failure
		}
		detected = result.Languages
	}
	prepared := &PreparedContent{}
	var rawTitle any
	if content.Title != nil {
		rawTitle = *content.Title
	}
	title, err := CoerceText(rawTitle)
	if err != nil {
		return nil, err
	}
	prepared.Fields.Titles = BuildTitles(title)
	if prepared.Fields.Titles == nil {
		prepared.Fields.Titles = []string{}
	}
	prepared.Fields.Locales = BuildLocales(language, detected)
	prepared.Fields.OccupationID, prepared.Fields.SeniorityID, err = p.Lookups.ResolveTitles(p.Matcher, prepared.Fields.Titles, "")
	if err != nil {
		return nil, err
	}
	locations, err := CoerceLocations(content.Locations)
	if err != nil {
		return nil, err
	}
	prepared.Fields.LocationIDs, prepared.Fields.LocationTypes, err = p.Locations.Resolve(ctx, locations, "", optionalText(language))
	if err != nil {
		return nil, err
	}
	text, err := CoerceText(optionalText(description))
	if err != nil {
		return nil, err
	}
	derived, err := p.Lookups.DescriptionFields(p.Matcher, optionalText(text))
	if err != nil {
		return nil, err
	}
	prepared.Fields.TechnologyIDs = derived.TechnologyIDs
	prepared.Fields.SalaryMin, prepared.Fields.SalaryMax = derived.SalaryMin, derived.SalaryMax
	prepared.Fields.SalaryCurrency, prepared.Fields.SalaryPeriod = derived.SalaryCurrency, derived.SalaryPeriod
	prepared.Fields.SalaryEUR = derived.SalaryEUR
	prepared.Fields.ExperienceMin, prepared.Fields.ExperienceMax = derived.ExperienceMin, derived.ExperienceMax
	// Rich persistence strips surrounding whitespace after normalization before
	// hashing; this is the same scalar coercion used by inserts and refreshes.
	prepared.Description, err = StageDescription(optionalText(text), optionalText(language))
	if err != nil {
		return nil, err
	}
	return prepared, ctx.Err()
}
