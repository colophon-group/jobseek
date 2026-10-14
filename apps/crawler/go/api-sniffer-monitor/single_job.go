package apisniffer

import (
	"context"
	"unicode/utf8"
)

var singleJobDescriptions = []string{"description", "body", "content", "bodyHtml", "body_html", "descriptionHtml", "description_html", "text", "details", "job_description", "jobDescription", "position_description_html", "summary", "Job_Posting_Description__c"}
var singleJobLocations = []string{"location", "locations", "locationName", "location_name", "office", "offices", "city", "cities", "place", "Location__c"}

func singleJobScore(object map[string]any) int {
	title := false
	for key := range object {
		if autoFieldPatterns["title"].MatchString(key) {
			title = true
			break
		}
	}
	if !title {
		return 0
	}
	score := 10
	for _, key := range singleJobDescriptions {
		if value, ok := object[key].(string); ok && utf8.RuneCountInString(value) > 50 {
			score += 20
			break
		}
	}
	for _, key := range singleJobLocations {
		if _, present := object[key]; present {
			score += 5
			break
		}
	}
	if len(object) >= 5 {
		score += 5
	}
	for _, value := range object {
		if array, ok := value.([]any); ok && len(array) > 10 {
			objects := 0
			for _, item := range array {
				if _, ok := item.(map[string]any); ok {
					objects++
				}
			}
			if objects > 10 {
				score -= 15
			}
		}
	}
	return score
}

// FindSingleJob preserves the original detail scraper's response ordering,
// explicit-path preference and bounded envelope traversal. The returned
// document retains provider object order for subsequent field extraction.
// Selection does not grant network, publisher-policy or write authority.
func FindSingleJob(ctx context.Context, documents []*Document, path string) (*Document, map[string]any, error) {
	if ctx == nil {
		return nil, nil, ErrField
	}
	var selectedDocument *Document
	var selected map[string]any
	best := 0
	consider := func(document *Document, object map[string]any, bonus int) {
		if score := singleJobScore(object); score > 0 && score+bonus > best {
			selectedDocument, selected, best = document, object, score+bonus
		}
	}
	for _, document := range documents {
		if err := ctx.Err(); err != nil {
			return nil, nil, err
		}
		if document == nil {
			continue
		}
		body, ok := document.Value.(map[string]any)
		if !ok {
			continue
		}
		if path != "" {
			value, err := Search(body, path)
			if err != nil {
				return nil, nil, err
			}
			if object, ok := value.(map[string]any); ok && singleJobScore(object) > 0 {
				consider(document, object, 50)
				continue
			}
		}
		consider(document, body, 0)
		for _, key := range []string{"data", "result", "job", "posting", "position", "details", "funnel"} {
			if nested, ok := body[key].(map[string]any); ok {
				consider(document, nested, 0)
				for _, child := range []string{"jobPosting", "job", "posting", "position", "result"} {
					if object, ok := nested[child].(map[string]any); ok {
						consider(document, object, 0)
					}
				}
			}
		}
		type entry struct {
			value any
			depth int
		}
		stack := []entry{{body, 0}}
		for visited := 0; len(stack) > 0 && visited < 512; visited++ {
			if err := ctx.Err(); err != nil {
				return nil, nil, err
			}
			current := stack[len(stack)-1]
			stack = stack[:len(stack)-1]
			if current.depth >= 8 {
				continue
			}
			switch value := current.value.(type) {
			case map[string]any:
				if current.depth > 0 {
					consider(document, value, 0)
				}
				keys := document.ObjectKeys(value)
				if len(keys) != len(value) {
					return nil, nil, ErrInventory
				}
				for _, key := range keys {
					stack = append(stack, entry{value[key], current.depth + 1})
				}
			case []any:
				if len(value) <= 50 {
					for _, child := range value {
						stack = append(stack, entry{child, current.depth + 1})
					}
				}
			}
		}
	}
	return selectedDocument, selected, nil
}

// ProjectSingleJobDetail reuses the existing HTTP detail field language. A
// heuristic title must be unambiguous; otherwise callers need explicit fields
// rather than depending on Python's randomized set iteration.
func ProjectSingleJobDetail(document *Document, object map[string]any, fields map[string]any) (map[string]any, error) {
	if document == nil || object == nil {
		return map[string]any{}, nil
	}
	if len(fields) > 0 {
		return projectSingleJobFields(document, object, fields)
	}
	out := map[string]any{}
	titleKey := ""
	for key := range object {
		if autoFieldPatterns["title"].MatchString(key) {
			if titleKey != "" {
				return nil, ErrAmbiguousAutoFields
			}
			titleKey = key
		}
	}
	if value, ok := object[titleKey].(string); ok {
		out["title"] = value
	}
	for _, key := range singleJobDescriptions {
		if value, ok := object[key].(string); ok && utf8.RuneCountInString(value) > 10 {
			out["description"] = value
			break
		}
	}
	for _, key := range singleJobLocations {
		value, present := object[key]
		if !present {
			continue
		}
		switch value := value.(type) {
		case string:
			out["locations"] = []any{value}
		case []any:
			if len(value) > 0 {
				if _, ok := value[0].(string); ok {
					out["locations"] = value
				} else if _, ok := value[0].(map[string]any); ok {
					locations := []any{}
					for _, item := range value {
						location, ok := item.(map[string]any)
						if !ok {
							return nil, ErrField
						}
						for _, key := range []string{"name", "title", "city", "label", "displayName"} {
							if text, ok := location[key].(string); ok {
								locations = append(locations, text)
								break
							}
						}
					}
					if len(locations) > 0 {
						out["locations"] = locations
					}
				}
			}
		}
		break
	}
	for target, keys := range map[string][]string{
		"employment_type": {"employment_type", "employmentType", "type", "job_type", "jobType", "work_type", "workType", "contract_type", "contractType"},
		"date_posted":     {"date_posted", "datePosted", "posted_at", "postedAt", "published_at", "publishedAt", "created_at", "createdAt", "publish_date"},
	} {
		for _, key := range keys {
			if value, present := object[key]; present {
				if text, ok := value.(string); ok {
					out[target] = text
				}
				break
			}
		}
	}
	for _, key := range []string{"job_location_type", "jobLocationType", "workplace_type", "workplaceType", "remote_type", "locationType", "isRemote", "remote"} {
		if value, present := object[key]; present {
			switch value := value.(type) {
			case string:
				out["job_location_type"] = value
			case bool:
				out["job_location_type"] = "False"
				if value {
					out["job_location_type"] = "True"
				}
			}
			break
		}
	}
	return out, nil
}
