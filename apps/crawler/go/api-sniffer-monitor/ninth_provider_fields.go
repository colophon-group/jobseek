package apisniffer

import (
	"net/url"
	"sort"
	"strings"
)

var ninthBeeLanguages = map[string]string{"0": "en", "1": "fr", "2": "nl", "3": "de", "4": "pt", "5": "es", "6": "it"}

func ninthFirst(values ...any) any {
	for _, value := range values {
		if detailTruthy(value) {
			return value
		}
	}
	return nil
}

func (d *Document) beeLocalized(value, language any) string {
	if text, ok := value.(string); ok {
		return strings.TrimSpace(text)
	}
	m := seventhObject(value)
	preferred, _ := d.String(language)
	for _, key := range []string{preferred, "0"} {
		if text := seventhTrim(m[key]); text != "" {
			return text
		}
	}
	// Decoding records source order, matching Python's first populated language.
	for _, key := range d.ObjectKeys(m) {
		if text := seventhTrim(m[key]); text != "" {
			return text
		}
	}
	return ""
}

func ninthWTTJLocalized(value any, language string) string {
	if text, ok := value.(string); ok {
		return text
	}
	m := seventhObject(value)
	for _, key := range []string{language, "en", "fr", "es", "cs", "sk"} {
		if text, ok := m[key].(string); ok && text != "" {
			return text
		}
	}
	return ""
}

func (d *Document) NinthProviderJobFields(raw any, o NinthProviderOptions) (map[string]any, error) {
	m, ok := raw.(map[string]any)
	if !ok {
		return nil, nil
	}
	out, metadata := map[string]any{}, map[string]any{}
	switch o.Provider {
	case "beehire":
		link := seventhTrim(m["inviteLink"])
		if link == "" {
			if key := seventhTrim(m["inviteKey"]); key != "" {
				link = "/invite/" + key
			}
		}
		title := d.beeLocalized(m["title"], m["language"])
		if link == "" || title == "" {
			return nil, nil
		}
		base, _ := url.Parse(o.Origin + "/")
		ref, err := url.Parse(link)
		if err != nil {
			return nil, ErrInventory
		}
		out["url"], out["title"] = base.ResolveReference(ref).String(), title
		if description := d.beeLocalized(ninthFirst(m["fullDescription"], m["description"]), m["language"]); description != "" {
			out["description"] = description
		} else if description = d.beeLocalized(m["description"], m["language"]); description != "" {
			out["description"] = description
		}
		location := seventhObject(m["location"])
		if name := seventhTrim(location["name"]); name != "" {
			out["locations"] = []string{name}
		} else if detailTruthy(location["isWorldwide"]) {
			out["locations"] = []string{"Worldwide"}
		} else {
			parts := []string{}
			seen := map[string]bool{}
			for _, key := range []string{"city", "state", "country"} {
				if part := seventhTrim(location[key]); part != "" && !seen[part] {
					seen[part], parts = true, append(parts, part)
				}
			}
			if len(parts) > 0 {
				out["locations"] = []string{strings.Join(parts, ", ")}
			}
		}
		contract := seventhObject(seventhObject(m["details"])["contract"])
		out["employment_type"], out["job_location_type"] = ninthFirst(contract["type"], contract["duration"]), contract["remote"]
		out["date_posted"] = ninthFirst(m["created"], m["createdAt"])
		language, _ := d.String(m["language"])
		if code := ninthBeeLanguages[language]; code != "" {
			out["language"] = code
		}
		titles := seventhObject(m["title"])
		descriptions, ok := m["fullDescription"].(map[string]any)
		if !ok {
			descriptions = seventhObject(m["description"])
		}
		localizations := map[string]any{}
		for key, locale := range ninthBeeLanguages {
			title, description := seventhTrim(titles[key]), seventhTrim(descriptions[key])
			if title != "" || description != "" {
				var t, desc any
				if title != "" {
					t = title
				}
				if description != "" {
					desc = description
				}
				localizations[locale] = map[string]any{"title": t, "description": desc, "locations": out["locations"]}
			}
		}
		if len(localizations) > 0 {
			out["localizations"] = localizations
		}
		metadata["id"], metadata["invite_key"], metadata["deadline"] = ninthFirst(m["id"], m["_id"]), m["inviteKey"], m["deadline"]
		if len(contract) > 0 {
			metadata["contract"] = contract
		}
		categories := []any{}
		for _, row := range seventhRows(m["jobCategories"]) {
			if value := seventhObject(row)["label"]; detailTruthy(value) {
				categories = append(categories, value)
			}
		}
		if len(categories) > 0 {
			metadata["categories"] = categories
		}
	case "hirehive":
		source, ok := m["hosted_url"].(string)
		if !ok || source == "" {
			return nil, nil
		}
		out["url"], out["title"], out["date_posted"] = source, m["title"], m["published_date"]
		if description, ok := m["description"].(map[string]any); ok {
			out["description"] = ninthFirst(description["html"], description["text"])
		} else {
			out["description"] = ninthFirst(m["description"])
		}
		if location := seventhTrim(m["location"]); location != "" {
			out["locations"] = []string{location}
		} else {
			parts := []string{}
			for _, value := range []any{m["state_code"], seventhObject(m["country"])["name"]} {
				if s, ok := value.(string); ok && s != "" {
					parts = append(parts, s)
				}
			}
			if len(parts) > 0 {
				out["locations"] = []string{strings.Join(parts, ", ")}
			}
		}
		kind := seventhObject(m["type"])
		out["employment_type"] = ninthFirst(kind["type"], kind["name"])
		// Shared native enum and salary normalization occurs in the worker.
		out["job_location_type"] = o.DefaultLocationType
		if code, ok := seventhObject(m["language"])["code"].(string); ok && code != "" {
			code = strings.ToLower(strings.Split(strings.Split(code, "-")[0], "_")[0])
			if len(code) == 2 {
				out["language"] = code
			}
		}
		for _, row := range seventhRows(m["compensation_tiers"]) {
			tier := seventhObject(row)
			if tier["min_value"] != nil || tier["max_value"] != nil {
				unit := ADPSalaryUnit(tier["interval"])
				if unit == "" {
					unit = "year"
				}
				out["base_salary"] = map[string]any{"currency": tier["currency_code"], "min": tier["min_value"], "max": tier["max_value"], "unit": unit}
				break
			}
		}
		if detailTruthy(m["id"]) {
			metadata["id"] = m["id"]
		}
		if value := seventhObject(m["category"])["name"]; detailTruthy(value) {
			metadata["category"] = value
		}
		if value := seventhObject(m["experience"])["type"]; detailTruthy(value) {
			metadata["experience"] = value
		}
	case "welcometothejungle":
		status, _ := m["status"].(string)
		if status == "archived" || status == "expired" || status == "closed" {
			return nil, nil
		}
		slug, _ := m["slug"].(string)
		title, _ := m["name"].(string)
		if slug == "" || title == "" {
			return nil, nil
		}
		if !ninthSlug.MatchString(slug) {
			return nil, ErrInventory
		}
		out["url"], out["title"] = o.Origin+"/"+o.Locale+"/companies/"+o.Slug+"/jobs/"+slug, title
		out["description"], out["employment_type"], out["date_posted"] = ninthFirst(m["description"]), ninthFirst(m["contract_type"]), ninthFirst(m["published_at"])
		language, ok := m["language"].(string)
		if !ok {
			language = o.Locale
		}
		out["language"], out["job_location_type"] = language, m["remote"]
		offices, ok := m["offices"].([]any)
		if !ok {
			offices = []any{m["office"]}
		}
		locations, seen := []string{}, map[string]bool{}
		for _, row := range offices {
			item := seventhObject(row)
			value, ok := ninthFirst(item["local_address"], item["address"], item["city"]).(string)
			if ok && value != "" && !seen[value] {
				seen[value], locations = true, append(locations, value)
			}
		}
		if len(locations) > 0 {
			out["locations"] = locations
		}
		if m["salary_min"] != nil || m["salary_max"] != nil {
			unit := ADPSalaryUnit(m["salary_period"])
			if unit == "" {
				unit = "year"
			}
			out["base_salary"] = map[string]any{"currency": m["salary_currency"], "min": m["salary_min"], "max": m["salary_max"], "unit": unit}
		}
		extras := map[string]any{}
		if profile, ok := m["profile"].(string); ok && profile != "" {
			extras["qualifications"] = profile
		}
		if missions, ok := m["key_missions"].([]any); ok && len(missions) > 0 {
			extras["responsibilities"] = missions
		}
		skills := []string{}
		for _, row := range seventhRows(m["skills"]) {
			if skill := ninthWTTJLocalized(seventhObject(row)["name"], language); skill != "" {
				skills = append(skills, skill)
			}
		}
		if len(skills) > 0 {
			extras["skills"] = skills
		}
		if len(extras) > 0 {
			out["extras"] = extras
		}
		metadata["reference"], metadata["start_date"] = m["reference"], m["start_date"]
		if profession := ninthWTTJLocalized(seventhObject(m["profession"])["name"], language); profession != "" {
			metadata["profession"] = profession
		}
	default:
		return nil, ErrOptions
	}
	for key, value := range metadata {
		if value == nil || value == "" {
			delete(metadata, key)
		}
	}
	if len(metadata) > 0 {
		out["metadata"] = metadata
	}
	return out, nil
}

func WTTJSummaries(hits []any, publicSlug string) ([]map[string]any, error) {
	out := []map[string]any{}
	byID := map[string]int{}
	for _, raw := range hits {
		hit := seventhObject(raw)
		slug, _ := hit["slug"].(string)
		if slug == "" {
			continue
		}
		if !ninthSlug.MatchString(slug) {
			return nil, ErrInventory
		}
		key, _ := hit["reference"].(string)
		if key == "" {
			key = slug
		}
		if index, exists := byID[key]; exists {
			if seventhObject(hit["website"])["reference"] == publicSlug && seventhObject(out[index]["website"])["reference"] != publicSlug {
				out[index] = hit
			}
		} else {
			byID[key], out = len(out), append(out, hit)
		}
	}
	return out, nil
}

// Localization maps become deterministic title/locale vectors for the existing
// rich writer; localized descriptions retain Python's primary scalar contract.
func BeehireLocalizationVectors(fields map[string]any) ([]string, []string) {
	m := seventhObject(fields["localizations"])
	keys := []string{}
	for key := range m {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	titles := []string{}
	for _, key := range keys {
		if title := seventhTrim(seventhObject(m[key])["title"]); title != "" {
			titles = append(titles, title)
		}
	}
	return titles, keys
}
