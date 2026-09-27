// Package smartrecruiters preserves the publication, localized requisition and
// requisition/location identities of the existing production provider.
package smartrecruiters

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

type Object = map[string]any

// Job preserves null versus empty fields; the existing writer owns enrichment.
type Job struct {
	URL             string `json:"url"`
	Title           any    `json:"title"`
	Description     any    `json:"description"`
	Locations       any    `json:"locations"`
	EmploymentType  any    `json:"employment_type"`
	JobLocationType any    `json:"job_location_type"`
	DatePosted      any    `json:"date_posted"`
	BaseSalary      any    `json:"base_salary"`
	Language        any    `json:"language"`
	Localizations   any    `json:"localizations"`
	Extras          any    `json:"extras"`
	Metadata        Object `json:"metadata"`
	SourceIdentity  any    `json:"source_identity"`
}

//go:embed salary_units.json
var salaryUnitsJSON []byte
var salaryUnits struct {
	Direct     map[string]string `json:"direct"`
	Substrings [][2]string       `json:"substrings"`
}

func init() {
	if err := json.Unmarshal(salaryUnitsJSON, &salaryUnits); err != nil {
		panic(err)
	}
}

func pySpace(r rune) bool  { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
func trim(s string) string { return strings.TrimFunc(s, pySpace) }
func length(s string) int  { return utf8.RuneCountInString(s) }
func object(v any) Object  { m, _ := v.(map[string]any); return m }
func truth(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case string:
		return x != ""
	case json.Number:
		return x != "0" && x != "0.0" && x != "-0.0"
	case []any:
		return len(x) > 0
	case []string:
		return len(x) > 0
	case map[string]any:
		return len(x) > 0
	}
	return true
}
func pyString(v any) string {
	switch x := v.(type) {
	case nil:
		return "None"
	case string:
		return x
	case bool:
		if x {
			return "True"
		}
		return "False"
	case json.Number:
		return x.String()
	}
	return fmt.Sprint(v)
}
func requireObject(value any, what string) (Object, error) {
	result, ok := value.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%s must be an object", what)
	}
	return result, nil
}
func defaultObject(m Object, key string) (Object, error) {
	v, ok := m[key]
	if !ok {
		return Object{}, nil
	}
	return requireObject(v, key)
}
func optionalText(v any) (string, error) {
	if v == nil {
		return "", nil
	}
	s, ok := v.(string)
	if !ok {
		return "", errors.New("expected text")
	}
	return s, nil
}

func ParseDetail(posting Object) (Job, error) {
	job := Job{Title: posting["name"], DatePosted: posting["releasedDate"]}
	adValue := posting["jobAd"]
	if truth(adValue) {
		ad, err := requireObject(adValue, "jobAd")
		if err != nil {
			return job, err
		}
		sections, err := defaultObject(ad, "sections")
		if err != nil {
			return job, err
		}
		parts := []string{}
		for _, key := range []string{"companyDescription", "jobDescription", "qualifications", "additionalInformation"} {
			section, ok := sections[key].(map[string]any)
			if !ok {
				continue
			}
			if !truth(section["text"]) {
				continue
			}
			text, err := optionalText(section["text"])
			if err != nil {
				return job, err
			}
			if truth(section["title"]) {
				text = "<h3>" + pyString(section["title"]) + "</h3>\n" + text
			}
			parts = append(parts, text)
		}
		if len(parts) > 0 {
			job.Description = strings.Join(parts, "\n")
		}
	}
	loc, err := defaultObject(posting, "location")
	if err != nil {
		return job, err
	}
	if truth(loc["fullLocation"]) {
		job.Locations = []any{loc["fullLocation"]}
	} else {
		parts := []string{}
		for _, key := range []string{"city", "region", "country"} {
			if truth(loc[key]) {
				v, err := optionalText(loc[key])
				if err != nil {
					return job, err
				}
				parts = append(parts, v)
			}
		}
		if len(parts) > 0 {
			job.Locations = []string{strings.Join(parts, ", ")}
		}
	}
	if truth(loc["remote"]) {
		job.JobLocationType = "remote"
	} else if truth(loc["hybrid"]) {
		job.JobLocationType = "hybrid"
	}
	if employment := object(posting["typeOfEmployment"]); employment != nil {
		job.EmploymentType = employment["label"]
	}
	for _, key := range []string{"department", "function", "experienceLevel"} {
		if value := object(posting[key]); truth(value["label"]) {
			if job.Metadata == nil {
				job.Metadata = Object{}
			}
			job.Metadata[key] = value["label"]
		}
	}
	if truth(posting["compensation"]) {
		comp, err := requireObject(posting["compensation"], "compensation")
		if err != nil {
			return job, err
		}
		if truth(comp["salary"]) {
			salary, err := requireObject(comp["salary"], "salary")
			if err != nil {
				return job, err
			}
			if salary["min"] != nil || salary["max"] != nil {
				period, err := optionalText(salary["period"])
				if err != nil {
					return job, err
				}
				period = strings.ToLower(trim(period))
				unit := salaryUnits.Direct[period]
				if unit == "" {
					for _, pair := range salaryUnits.Substrings {
						if strings.Contains(period, pair[0]) {
							unit = pair[1]
							break
						}
					}
				}
				if unit == "" {
					unit = "year"
				}
				job.BaseSalary = Object{"currency": salary["currency"], "min": salary["min"], "max": salary["max"], "unit": unit}
			}
		}
	}
	return job, nil
}

func decodeObject(body []byte) (Object, error) {
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	var m Object
	if err := decoder.Decode(&m); err != nil {
		return nil, err
	}
	if m == nil {
		return nil, errors.New("expected object")
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return nil, errors.New("trailing JSON")
	}
	return m, nil
}
func integer(v any) (int, error) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, errors.New("expected integer")
	}
	i, err := strconv.Atoi(n.String())
	return i, err
}
