package executor

import (
	"context"
	"errors"
	"sort"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

// NativeLookups is one immutable process snapshot, matching the Python
// executor's lazy singleton lifetime. Location backfill has its own serialized
// index; these rows are loaded sequentially through the same bounded pool.
type NativeLookups struct {
	technologies, occupations, seniorities map[string]int64
	rates                                  map[string]float64
}

func LoadLookups(ctx context.Context, store *Store) (*NativeLookups, error) {
	if store == nil {
		return nil, errors.New("missing executor store")
	}
	out := &NativeLookups{}
	for _, target := range []struct {
		query       string
		destination *map[string]int64
	}{
		{"SELECT id, slug FROM technology", &out.technologies},
		{"SELECT id, slug FROM occupation", &out.occupations},
		{"SELECT id, slug FROM seniority", &out.seniorities},
	} {
		rows, err := store.pool.Query(ctx, target.query)
		if err != nil {
			return nil, err
		}
		values := map[string]int64{}
		for rows.Next() {
			var id int64
			var slug string
			if err := rows.Scan(&id, &slug); err != nil {
				rows.Close()
				return nil, err
			}
			values[slug] = id
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return nil, err
		}
		*target.destination = values
	}
	rows, err := store.pool.Query(ctx, "SELECT currency, to_eur FROM currency_rate")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out.rates = map[string]float64{}
	for rows.Next() {
		var currency string
		var rate float64
		if err := rows.Scan(&currency, &rate); err != nil {
			return nil, err
		}
		out.rates[currency] = rate
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return out, nil
}

// ResolveTitles selects the first matching database ID independently for each
// dimension. An unmapped slug does not stop a later title, and an internship
// signal overrides even a title-derived seniority with an unmapped (NULL) ID.
func (l *NativeLookups) ResolveTitles(matcher *enrichment.Matcher, titles []string, employmentType string) (*int64, *int64, error) {
	if l == nil || matcher == nil {
		return nil, nil, errors.New("missing native taxonomy")
	}
	result, err := matcher.Process(enrichment.Request{Operation: "occupation_seniority", Titles: titles, EmploymentType: employmentType})
	if err != nil {
		return nil, nil, err
	}
	var occupation, seniority *int64
	for _, title := range result.Titles {
		if occupation == nil && title.Occupation != nil {
			if id, ok := l.occupations[*title.Occupation]; ok {
				occupation = &id
			}
		}
		if seniority == nil && title.Seniority != nil {
			if id, ok := l.seniorities[*title.Seniority]; ok {
				seniority = &id
			}
		}
		if occupation != nil && seniority != nil {
			break
		}
	}
	if result.Intern {
		seniority = nil
		if id, ok := l.seniorities["intern"]; ok {
			seniority = &id
		}
	}
	return occupation, seniority, nil
}

func (l *NativeLookups) ResolveTechnologies(matcher *enrichment.Matcher, description string) ([]int64, error) {
	if l == nil || matcher == nil {
		return nil, errors.New("missing native taxonomy")
	}
	seen := map[int64]bool{}
	for _, slug := range matcher.Technologies(description) {
		if id, ok := l.technologies[slug]; ok {
			seen[id] = true
		}
	}
	var ids []int64
	for id := range seen {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	return ids, nil
}

func (l *NativeLookups) DescriptionFields(matcher *enrichment.Matcher, description string) (ContentFields, error) {
	var fields ContentFields
	ids, err := l.ResolveTechnologies(matcher, description)
	if err != nil {
		return fields, err
	}
	fields.TechnologyIDs = ids
	salary, err := enrichment.Salary(description, l.rates)
	if err != nil {
		return fields, err
	}
	if salary != nil && salary.Unified != nil {
		rangeValue := salary.Unified
		fields.SalaryMin, fields.SalaryMax = &rangeValue.Min, rangeValue.Max
		fields.SalaryCurrency, fields.SalaryPeriod = &rangeValue.Currency, &rangeValue.Period
		fields.SalaryEUR = salary.EUR
	}
	fields.ExperienceMin, fields.ExperienceMax = enrichment.Experience(description)
	return fields, nil
}
