package apisniffer

import (
	"errors"
	"regexp"
)

var ErrAmbiguousAutoFields = errors.New("ambiguous automatic API field mapping")
var autoFieldPatterns = map[string]*regexp.Regexp{
	"title":             regexp.MustCompile(`(?i)^(title|name|job_?title|position_?title|label|heading|role|job_?name|job_?opening_?name|Title__c)$`),
	"description":       regexp.MustCompile(`(?i)^(description|body|content|bodyHtml|body_?html|descriptionHtml|description_?html|text|details|job_?description|position_?description_?html|summary|Job_Posting_Description__c)$`),
	"employment_type":   regexp.MustCompile(`(?i)^(employment_?type|type|job_?type|work_?type|contract_?type|employmentType|workType|employment_?status_?label)$`),
	"date_posted":       regexp.MustCompile(`(?i)^(date_?posted|posted_?at|posted_?date|published_?at|created_?at|datePosted|publishedAt|createdAt|publish_?date|publication_date__c)$`),
	"job_location_type": regexp.MustCompile(`(?i)^(job_?location_?type|workplace_?type|remote_?type|location_?type|workplaceType|locationType|isRemote|remote|Modality__c)$`),
	"metadata.team":     regexp.MustCompile(`(?i)^(team|department|group|division|org|organization|category|departmentName|teamName|team_?name|department_?name|department_?label)$`),
}
var autoLocationKey = regexp.MustCompile(`(?i)^(location|locations|office|offices|city|cities|place|places|requisition_?locations|work_?locations|Location__c)$`)
var autoLocationPart = regexp.MustCompile(`(?i)^(name|title|city|label|display_?name|displayName|value)$`)

type inventorySourceRow struct {
	document *Document
	row      map[string]any
}

// Python collects the first five items' keys in a set. A unique match preserves
// its result across hash seeds; multiple matches require explicit configuration
// rather than committing an arbitrary title, description or department.
func autoMapFields(rows []inventorySourceRow) (map[string]any, error) {
	mapping := map[string]any{}
	sample := rows[:min(len(rows), 5)]
	keys := map[string]bool{}
	for _, source := range sample {
		for key := range source.row {
			keys[key] = true
		}
	}
	workLevel := false
	for _, source := range sample {
		if value, ok := source.row["workLevelCode"].(string); ok && value != "" {
			workLevel = true
			break
		}
	}
	for field, pattern := range autoFieldPatterns {
		if field == "employment_type" && workLevel {
			mapping[field] = "workLevelCode"
			continue
		}
		match := ""
		for key := range keys {
			if pattern.MatchString(key) {
				if match != "" {
					return nil, ErrAmbiguousAutoFields
				}
				match = key
			}
		}
		if match != "" {
			mapping[field] = match
		}
	}
	for _, source := range sample {
		locations, ok := source.row["requisitionLocations"].([]any)
		if !ok || len(locations) == 0 {
			continue
		}
		first, ok := locations[0].(map[string]any)
		if !ok {
			break
		}
		address, ok := first["address"].(map[string]any)
		if !ok {
			break
		}
		if _, ok = address["cityName"].(string); ok {
			mapping["locations"] = "requisitionLocations[].join(', ', [address.cityName, address.countrySubdivisionLevel1.codeValue, address.country.longName][?@])"
		}
		break
	}
	if mapping["locations"] == nil {
		key := ""
		for candidate := range keys {
			if autoLocationKey.MatchString(candidate) {
				if key != "" {
					return nil, ErrAmbiguousAutoFields
				}
				key = candidate
			}
		}
		if key != "" {
			for _, source := range sample {
				value, exists := source.row[key]
				if !exists {
					continue
				}
				switch first := value.(type) {
				case string:
					mapping["locations"] = key
				case []any:
					if len(first) > 0 {
						switch item := first[0].(type) {
						case string:
							mapping["locations"] = key
						case map[string]any:
							if nameCode, ok := item["nameCode"].(map[string]any); ok {
								for _, part := range []string{"shortName", "longName"} {
									if _, ok := nameCode[part].(string); ok {
										mapping["locations"] = key + "[].nameCode." + part
										break
									}
								}
							}
							if mapping["locations"] == nil {
								if part := autoLocationSubfield(source.document, item, false); part != "" {
									mapping["locations"] = key + "[]." + part
								}
							}
						}
					}
				case map[string]any:
					if part := autoLocationSubfield(source.document, first, true); part != "" {
						mapping["locations"] = key + "." + part
					}
				}
				break
			}
		}
	}
	if raw, ok := mapping["metadata.team"].(string); ok {
		for _, source := range sample {
			value, exists := source.row[raw]
			if !exists {
				continue
			}
			if inner, ok := value.(map[string]any); ok {
				delete(mapping, "metadata.team")
				for _, part := range []string{"name", "title", "label"} {
					if _, ok := inner[part]; ok {
						mapping["metadata.team"] = raw + "." + part
						break
					}
				}
				if mapping["metadata.team"] == nil {
					for _, part := range source.document.ObjectKeys(inner) {
						if _, ok := inner[part].(string); ok {
							mapping["metadata.team"] = raw + "." + part
							break
						}
					}
				}
			}
			break
		}
	}
	return mapping, nil
}
func autoLocationSubfield(d *Document, object map[string]any, nonempty bool) string {
	for _, key := range d.ObjectKeys(object) {
		if autoLocationPart.MatchString(key) {
			return key
		}
	}
	for _, key := range d.ObjectKeys(object) {
		if value, ok := object[key].(string); ok && (!nonempty || value != "") {
			return key
		}
	}
	return ""
}
