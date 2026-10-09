package apisniffer

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"golang.org/x/text/cases"
)

var portalPositiveID = regexp.MustCompile(`^[1-9][0-9]*$`)
var portalDatePrefix = regexp.MustCompile(`^(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{8}|[0-9]{4}-?W[0-9]{2}(?:-?[1-7])?)`)
var portalISOTime = regexp.MustCompile(`^([0-9]{2})(?::?([0-9]{2})(?::?([0-9]{2}))?)?(?:[.,][0-9]+)?(?:([+-])([0-9]{2})(?::?([0-9]{2})(?::?([0-9]{2}))?)?(?:[.,][0-9]+)?)?$`)

func portalClean(value any) string {
	s, _ := value.(string)
	return inlineNormalized(s)
}

func portalNumber(value any) (float64, bool) {
	n, ok := value.(json.Number)
	if !ok {
		return 0, false
	}
	f, err := n.Float64()
	return f, err == nil
}

func portalOptionalText(value string) any {
	if value == "" {
		return nil
	}
	return value
}

// Keep the original date's local day; timezone offsets do not shift it to UTC.
func KekaPublishedDate(value any) any {
	s := strings.ReplaceAll(portalClean(value), "Z", "+00:00")
	prefix := portalDatePrefix.FindString(s)
	if prefix == "" {
		return nil
	}
	var date time.Time
	var err error
	if strings.Contains(prefix, "W") {
		raw := strings.ReplaceAll(prefix, "-", "")
		year, _ := strconv.Atoi(raw[:4])
		week, _ := strconv.Atoi(raw[5:7])
		day := 1
		if len(raw) == 8 {
			day, _ = strconv.Atoi(raw[7:])
		}
		if year < 1 || week < 1 || week > 53 {
			return nil
		}
		jan4 := time.Date(year, time.January, 4, 0, 0, 0, 0, time.UTC)
		weekday := (int(jan4.Weekday()) + 6) % 7
		date = jan4.AddDate(0, 0, -weekday+7*(week-1)+day-1)
		y, w := date.ISOWeek()
		if y != year || w != week || date.Year() < 1 || date.Year() > 9999 {
			return nil
		}
	} else {
		layout := "2006-01-02"
		if len(prefix) == 8 {
			layout = "20060102"
		}
		date, err = time.Parse(layout, prefix)
		if err != nil || date.Year() < 1 {
			return nil
		}
	}
	if suffix := s[len(prefix):]; suffix != "" {
		_, size := utf8.DecodeRuneInString(suffix)
		match := portalISOTime.FindStringSubmatch(suffix[size:])
		if match == nil {
			return nil
		}
		for _, pair := range [][2]int{{1, 23}, {2, 59}, {3, 59}, {5, 23}, {6, 59}, {7, 59}} {
			if n, _ := strconv.Atoi(match[pair[0]]); n > pair[1] {
				return nil
			}
		}
	}
	return date.Format("2006-01-02")
}

func portalNormalize(value any, normalize SmallDescriptionNormalizer) (any, error) {
	s, ok := value.(string)
	if !ok {
		s = ""
	}
	if normalize == nil {
		return nil, ErrOptions
	}
	v, err := normalize(s)
	if err != nil || v == nil {
		return nil, err
	}
	return *v, nil
}

func KekaJobFields(value any, listing string, normalize SmallDescriptionNormalizer) (map[string]any, error) {
	raw, ok := value.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	id, ok := raw["id"].(json.Number)
	title := portalClean(raw["title"])
	if !ok || !portalPositiveID.MatchString(string(id)) || title == "" {
		return nil, ErrInventory
	}
	description, err := portalNormalize(raw["description"], normalize)
	if err != nil {
		return nil, err
	}
	metadata := map[string]any{"id": id}
	for _, pair := range [][2]string{{"departmentIdentifier", "department_id"}, {"departmentName", "department"}, {"experience", "experience"}, {"jobNumber", "job_number"}} {
		if s := portalClean(raw[pair[0]]); s != "" {
			metadata[pair[1]] = s
		}
	}
	var employment any
	jobType, numeric := portalNumber(raw["jobType"])
	if b, ok := raw["jobType"].(bool); ok && b {
		jobType, numeric = 1, true
	}
	if numeric && jobType == 2 {
		employment = "Full Time"
	} else if numeric && jobType == 1 {
		employment = "Part Time"
	}
	if n, ok := raw["jobType"].(json.Number); ok {
		if _, err := n.Int64(); err == nil {
			metadata["job_type"] = n
		}
	}
	locations, seenLocations := []string{}, map[string]bool{}
	rows, _ := raw["jobLocations"].([]any)
	for _, row := range rows {
		location, ok := row.(map[string]any)
		if !ok {
			continue
		}
		parts, seen := []string{}, map[string]bool{}
		for _, key := range []string{"city", "state", "countryName"} {
			text := portalClean(location[key])
			fold := cases.Fold().String(text)
			if text != "" && !seen[fold] {
				parts, seen[fold] = append(parts, text), true
			}
		}
		label := strings.Join(parts, ", ")
		if label == "" {
			label = portalClean(location["name"])
		}
		if label != "" && !seenLocations[label] {
			locations, seenLocations[label] = append(locations, label), true
		}
	}
	var salary any
	if r, ok := raw["salaryRange"].(map[string]any); ok {
		var minimum, maximum, unit any
		if v, ok := portalNumber(r["minimum"]); ok && v > 0 {
			minimum = r["minimum"]
		}
		if v, ok := portalNumber(r["maximum"]); ok && v > 0 {
			maximum = r["maximum"]
		}
		if n, ok := r["salaryPeriod"].(json.Number); ok {
			if integer, e := n.Int64(); e == nil {
				unit = portalOptionalText(map[int64]string{1: "hour", 3: "month", 4: "year"}[integer])
			}
		}
		if minimum != nil || maximum != nil {
			salary = map[string]any{"currency": portalOptionalText(portalClean(r["currency"])), "min": minimum, "max": maximum, "unit": unit}
		}
	}
	fields := map[string]any{"url": strings.TrimSuffix(listing, "/") + "/jobdetails/" + string(id), "title": title, "description": description, "employment_type": employment, "date_posted": KekaPublishedDate(raw["publishedOn"]), "base_salary": salary, "metadata": metadata}
	if len(locations) > 0 {
		fields["locations"] = locations
	}
	if skills := portalSkills(raw["skillNames"]); len(skills) > 0 {
		fields["extras"] = map[string]any{"skills": skills}
	}
	return fields, nil
}

func portalSkills(value any) []string {
	rows, _ := value.([]any)
	out, seen := []string{}, map[string]bool{}
	for _, row := range rows {
		if text := portalClean(row); text != "" && !seen[text] {
			out, seen[text] = append(out, text), true
		}
	}
	return out
}

func TurboHirePublicURL(origin, publicID string) string {
	var b strings.Builder
	for _, c := range []byte(enterpriseUnquote(publicID)) {
		if c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || strings.ContainsRune("-._~", rune(c)) {
			b.WriteByte(c)
		} else {
			fmt.Fprintf(&b, "%%%02X", c)
		}
	}
	return origin + "/job/publicjobs/" + b.String()
}

func TurboHireJobFields(raw map[string]any, origin string, normalize SmallDescriptionNormalizer) (map[string]any, error) {
	id, publicID, title := portalClean(raw["JobId"]), portalClean(raw["JobIdObfuscated"]), portalClean(raw["JobTitle"])
	if id == "" || publicID == "" || title == "" {
		return nil, ErrInventory
	}
	fields := map[string]any{"url": TurboHirePublicURL(origin, publicID), "title": title, "language": "en"}
	for _, key := range []string{"JobDescriptionV2", "JobDescription"} {
		if smallText(raw[key]) != "" {
			description, err := portalNormalize(raw[key], normalize)
			if err != nil {
				return nil, err
			}
			fields["description"] = description
			break
		}
	}
	metadata, extras := map[string]any{"id": id}, map[string]any{}
	for _, pair := range [][2]string{{"JobCode", "job_code"}, {"Department", "department"}, {"ClientName", "client_name"}} {
		if text := portalClean(raw[pair[0]]); text != "" {
			metadata[pair[1]] = text
		}
	}
	if experience, ok := raw["Experience"].(map[string]any); ok {
		for _, pair := range [][2]string{{"MinExp", "experience_min"}, {"MaxExp", "experience_max"}} {
			if _, ok := portalNumber(experience[pair[0]]); ok {
				metadata[pair[1]] = experience[pair[0]]
			}
		}
	}
	if skills := portalSkills(raw["Skills"]); len(skills) > 0 {
		extras["skills"] = skills
	}
	for _, pair := range [][2]string{{"RolesAndResponsibilitiesV2", "responsibilities"}, {"EligibilityV2", "qualifications"}} {
		if smallText(raw[pair[0]]) != "" {
			value, err := portalNormalize(raw[pair[0]], normalize)
			if err != nil {
				return nil, err
			}
			if value != nil && value != "" {
				extras[pair[1]] = value
			}
		}
	}
	employment := portalClean(raw["JobTypeV2"])
	if employment == "" {
		employment = portalClean(raw["Type"])
	}
	if cases.Fold().String(employment) != "unspecified" && employment != "" {
		fields["employment_type"] = employment
	}
	date := raw["PublishedDate"]
	if dates, ok := raw["PublishedDates"].(map[string]any); ok && detailTruthy(dates["CAREERPAGE"]) {
		date = dates["CAREERPAGE"]
	}
	if date, ok := date.(string); ok {
		fields["date_posted"] = date
	}
	if rawLocation, ok := raw["Location"].(string); ok && smallText(rawLocation) != "" {
		locations, seen := []string{}, map[string]bool{}
		document, err := Decode([]byte(rawLocation))
		if err != nil {
			locations = append(locations, portalClean(rawLocation))
		} else if rows, ok := document.Value.([]any); ok {
			for _, raw := range rows {
				row, _ := raw.(map[string]any)
				text := portalClean(row["Address"])
				fold := cases.Fold().String(text)
				if text != "" && !seen[fold] {
					locations, seen[fold] = append(locations, text), true
				}
			}
		}
		if len(locations) > 0 {
			fields["locations"] = locations
		}
	}
	fields["metadata"] = metadata
	if len(extras) > 0 {
		fields["extras"] = extras
	}
	return fields, nil
}
