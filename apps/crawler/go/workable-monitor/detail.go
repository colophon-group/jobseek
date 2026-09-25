package workable

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
)

// DetailContent is the Workable scraper's JobContent boundary. Shared
// enrichment and persistence still consume these fields after extraction.
type DetailContent struct {
	Title           *string           `json:"title,omitempty"`
	Description     *string           `json:"description,omitempty"`
	Locations       []string          `json:"locations,omitempty"`
	EmploymentType  *string           `json:"employment_type,omitempty"`
	JobLocationType *string           `json:"job_location_type,omitempty"`
	DatePosted      *string           `json:"date_posted,omitempty"`
	Metadata        map[string]string `json:"metadata,omitempty"`
}

func stringField(fields map[string]any, key string) (string, bool) {
	value, ok := fields[key].(string)
	return value, ok && value != ""
}

func truthyValue(value any) bool {
	switch value := value.(type) {
	case nil:
		return false
	case bool:
		return value
	case string:
		return value != ""
	case float64:
		return value != 0
	case []any:
		return len(value) > 0
	case map[string]any:
		return len(value) > 0
	default:
		return true
	}
}

func joinedLocation(value any) (string, error) {
	if value, ok := value.(string); ok {
		return value, nil
	}
	fields, ok := value.(map[string]any)
	if !ok {
		return "", errors.New("Workable location is not a string or object")
	}
	parts := make([]string, 0, 3)
	for _, key := range []string{"city", "region", "country"} {
		if raw, exists := fields[key]; exists && raw != nil && raw != "" {
			part, ok := raw.(string)
			if !ok {
				return "", fmt.Errorf("Workable location %s is not a string", key)
			}
			parts = append(parts, part)
		}
	}
	return strings.Join(parts, ", "), nil
}

func detailLocations(fields map[string]any) ([]string, error) {
	if raw, ok := fields["locations"].([]any); ok && len(raw) > 0 {
		seen := map[string]struct{}{}
		out := make([]string, 0, len(raw))
		for _, item := range raw {
			if _, ok := item.(string); !ok {
				if _, ok := item.(map[string]any); !ok {
					continue
				}
			}
			name, err := joinedLocation(item)
			if err != nil {
				return nil, err
			}
			if name != "" {
				if _, exists := seen[name]; !exists {
					out = append(out, name)
					seen[name] = struct{}{}
				}
			}
		}
		return out, nil
	}
	if raw, ok := fields["location"]; ok && raw != nil {
		if _, valid := raw.(string); !valid {
			if _, valid := raw.(map[string]any); !valid {
				return nil, nil
			}
		}
		name, err := joinedLocation(raw)
		if name != "" {
			return []string{name}, err
		}
		return nil, err
	}
	return nil, nil
}

func normalizeWorkplace(raw string) *string {
	key := strings.ToLower(strings.TrimSpace(raw))
	if cut := strings.Index(key, " ("); cut >= 0 {
		key = key[:cut]
	}
	var value string
	switch key {
	case "remote", "remote working", "telecommute", "work from home", "wfh", "fully remote", "100% remote":
		value = "remote"
	case "hybrid", "hybrid working", "flexible", "partially remote":
		value = "hybrid"
	case "onsite", "on-site", "on_site", "on site", "office", "in-office", "in_office", "in office", "in-person":
		value = "onsite"
	default:
		return nil
	}
	return &value
}

// ProjectDetail projects one exact JSON response with the Python scraper's
// raw description, location, type, and department rules.
func ProjectDetail(body []byte) (DetailContent, error) {
	var fields map[string]any
	if err := json.Unmarshal(body, &fields); err != nil || fields == nil {
		return DetailContent{}, errors.New("Workable detail is not a JSON object")
	}
	content := DetailContent{}
	if title, ok := fields["title"].(string); ok {
		content.Title = &title
	}
	parts := make([]string, 0, 3)
	for _, key := range []string{"description", "requirements", "benefits"} {
		if text, ok := stringField(fields, key); ok {
			parts = append(parts, text)
		}
	}
	if len(parts) > 0 {
		joined := strings.Join(parts, "\n")
		content.Description = &joined
	}
	locations, err := detailLocations(fields)
	if err != nil {
		return DetailContent{}, err
	}
	content.Locations = locations
	if value, ok := stringField(fields, "type"); ok {
		content.EmploymentType = &value
	}
	if workplace, ok := stringField(fields, "workplace"); ok {
		content.JobLocationType = normalizeWorkplace(workplace)
	}
	if content.JobLocationType == nil && truthyValue(fields["remote"]) {
		value := "remote"
		content.JobLocationType = &value
	}
	if date, ok := fields["published"].(string); ok {
		content.DatePosted = &date
	}
	if department, ok := stringField(fields, "department"); ok {
		content.Metadata = map[string]string{"department": department}
	} else if raw, ok := fields["department"].([]any); ok && len(raw) > 0 {
		departments := make([]string, 0, len(raw))
		for _, item := range raw {
			value, ok := item.(string)
			if !ok {
				return DetailContent{}, errors.New("Workable department contains a non-string value")
			}
			departments = append(departments, value)
		}
		content.Metadata = map[string]string{"department": strings.Join(departments, ", ")}
	}
	return content, nil
}

var (
	markdownHeading = regexp.MustCompile(`(?i)^(#{2,6})\s+(.+)$`)
	markdownBullet  = regexp.MustCompile(`^-\s+(.+)$`)
	markdownBold    = regexp.MustCompile(`\*\*(.+?)\*\*`)
	markdownRemote  = regexp.MustCompile(`(?i)\s+\((?:remote|hybrid|on-?site)\)$`)
	markdownPosted  = regexp.MustCompile(`^Posted\s+(\d{4}-\d{2}-\d{2})$`)
)

var pythonHTMLEscape = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&#x27;")

func markdownInline(raw string) string {
	return markdownBold.ReplaceAllString(pythonHTMLEscape.Replace(strings.TrimSpace(raw)), "<strong>$1</strong>")
}

func markdownFragment(raw string) *string {
	lines := strings.Split(strings.ReplaceAll(raw, "\r\n", "\n"), "\n")
	var out []string
	inList := false
	closeList := func() {
		if inList {
			out = append(out, "</ul>")
			inList = false
		}
	}
	for _, rawLine := range lines {
		line := strings.TrimSpace(rawLine)
		if line == "" {
			closeList()
			continue
		}
		if heading := markdownHeading.FindStringSubmatch(line); heading != nil {
			closeList()
			level := len(heading[1])
			out = append(out, fmt.Sprintf("<h%d>%s</h%d>", level, markdownInline(heading[2]), level))
			continue
		}
		if bullet := markdownBullet.FindStringSubmatch(line); bullet != nil {
			if !inList {
				out = append(out, "<ul>")
				inList = true
			}
			out = append(out, "<li>"+markdownInline(bullet[1])+"</li>")
			continue
		}
		closeList()
		out = append(out, "<p>"+markdownInline(line)+"</p>")
	}
	closeList()
	if len(out) == 0 {
		return nil
	}
	value := strings.Join(out, "\n")
	return &value
}

// ProjectMarkdownDetail preserves the public fallback's small Markdown subset.
func ProjectMarkdownDetail(body []byte) DetailContent {
	content := DetailContent{}
	lines := strings.Split(strings.NewReplacer("\r\n", "\n", "\r", "\n").Replace(string(body)), "\n")
	summarySeen := false
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if content.Title == nil && strings.HasPrefix(line, "# ") {
			value := strings.TrimSpace(strings.TrimPrefix(line, "# "))
			if value != "" {
				content.Title = &value
			}
		}
		if !summarySeen && strings.HasPrefix(line, ">") {
			summarySeen = true
			parts := strings.Split(strings.TrimSpace(strings.TrimPrefix(line, ">")), " · ")
			if len(parts) >= 4 {
				location := strings.TrimSpace(strings.Join(parts[1:len(parts)-2], " · "))
				location = markdownRemote.ReplaceAllString(location, "")
				if location != "" {
					content.Locations = []string{location}
				}
				if value := strings.TrimSpace(parts[len(parts)-2]); value != "" {
					content.EmploymentType = &value
				}
				if posted := markdownPosted.FindStringSubmatch(strings.TrimSpace(parts[len(parts)-1])); posted != nil {
					value := posted[1]
					content.DatePosted = &value
				}
			}
		}
		if content.JobLocationType == nil && strings.HasPrefix(strings.ToLower(trimmed), "**workplace:**") {
			content.JobLocationType = normalizeWorkplace(strings.TrimSpace(trimmed[len("**Workplace:**"):]))
		}
		if content.Metadata == nil && strings.HasPrefix(strings.ToLower(trimmed), "**department:**") {
			if value := strings.TrimSpace(trimmed[len("**Department:**"):]); value != "" {
				content.Metadata = map[string]string{"department": value}
			}
		}
	}
	for index, line := range lines {
		if strings.EqualFold(strings.TrimSpace(line), "## Description") {
			end := len(lines)
			for next := index + 1; next < len(lines); next++ {
				if strings.EqualFold(strings.TrimSpace(lines[next]), "## Apply") {
					end = next
					break
				}
			}
			content.Description = markdownFragment(strings.Join(lines[index+1:end], "\n"))
			break
		}
	}
	return content
}
