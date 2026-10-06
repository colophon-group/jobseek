package executor

import (
	_ "embed"
	"encoding/json"
	"errors"
	"html"
	"strconv"
	"strings"
	"unicode"

	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
)

//go:embed content_rules.json
var contentRulesJSON []byte
var contentRules struct {
	GarbageTitles   []string          `json:"garbage_titles"`
	EmploymentTypes map[string]string `json:"employment_types"`
	InternSignals   []string          `json:"intern_signals"`
}

func init() {
	if err := json.Unmarshal(contentRulesJSON, &contentRules); err != nil {
		panic(err)
	}
}

func pythonSpace(r rune) bool        { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
func pythonTrim(value string) string { return strings.TrimFunc(value, pythonSpace) }
func pythonLower(value string) string {
	return strings.ToLower(strings.ReplaceAll(value, "\u0130", "i\u0307"))
}

func CoerceText(value any) (*string, error) {
	if value == nil {
		return nil, nil
	}
	var text string
	switch typed := value.(type) {
	case string:
		text = pythonTrim(typed)
	case []string:
		converted := make([]any, len(typed))
		for i, v := range typed {
			converted[i] = v
		}
		return CoerceText(converted)
	case []any:
		var parts []string
		seen := map[string]bool{}
		for _, item := range typed {
			part, err := CoerceText(item)
			if err != nil {
				return nil, err
			}
			if part != nil && !seen[*part] {
				parts = append(parts, *part)
				seen[*part] = true
			}
		}
		text = strings.Join(parts, ", ")
	case map[string]any:
		canonical, err := b0task.CanonicalJSON(typed, true)
		if err != nil {
			return nil, err
		}
		var output strings.Builder
		quoted, escaped := false, false
		for _, c := range canonical {
			output.WriteByte(c)
			if quoted {
				if escaped {
					escaped = false
				} else if c == '\\' {
					escaped = true
				} else if c == '"' {
					quoted = false
				}
				continue
			}
			if c == '"' {
				quoted = true
			} else if c == ',' || c == ':' {
				output.WriteByte(' ')
			}
		}
		text = output.String()
	case bool:
		if typed {
			text = "True"
		} else {
			text = "False"
		}
	case json.Number:
		var err error
		text, err = b0task.CanonicalNumber(string(typed))
		if err != nil {
			return nil, err
		}
	case int:
		text = strconv.Itoa(typed)
	case int64:
		text = strconv.FormatInt(typed, 10)
	case float64:
		raw, err := json.Marshal(typed)
		if err != nil {
			return nil, err
		}
		if !strings.ContainsAny(string(raw), ".eE") {
			raw = append(raw, '.', '0')
		}
		text, err = b0task.CanonicalNumber(string(raw))
		if err != nil {
			return nil, err
		}
	default:
		return nil, errors.New("unsupported content scalar")
	}
	if text == "" {
		return nil, nil
	}
	return &text, nil
}

func CoerceLocations(value any) ([]string, error) {
	if value == nil {
		return nil, nil
	}
	if typed, ok := value.([]string); ok {
		converted := make([]any, len(typed))
		for i, v := range typed {
			converted[i] = v
		}
		value = converted
	}
	if items, ok := value.([]any); ok {
		var parts []string
		seen := map[string]bool{}
		for _, item := range items {
			text, err := CoerceText(item)
			if err != nil {
				return nil, err
			}
			if text != nil && !seen[*text] {
				parts = append(parts, *text)
				seen[*text] = true
			}
		}
		return parts, nil
	}
	text, err := CoerceText(value)
	if err != nil || text == nil {
		return nil, err
	}
	return []string{*text}, nil
}

func GarbageTitle(title string) bool {
	key := pythonLower(pythonTrim(title))
	for _, garbage := range contentRules.GarbageTitles {
		if key == garbage {
			return true
		}
	}
	return false
}

func EmploymentType(raw *string) *string {
	if raw == nil {
		return nil
	}
	key := pythonLower(pythonTrim(*raw))
	for _, signal := range contentRules.InternSignals {
		if key == signal {
			return nil
		}
	}
	if value, ok := contentRules.EmploymentTypes[key]; ok {
		return &value
	}
	return nil
}

// Detail scrapers preserve an internship signal for shared seniority matching;
// other commitments cross the existing normalized JobContent boundary.
func ScraperEmploymentType(raw *string) *string {
	if raw != nil {
		key := pythonLower(pythonTrim(*raw))
		for _, signal := range contentRules.InternSignals {
			if key == signal {
				return raw
			}
		}
	}
	return EmploymentType(raw)
}

func BuildTitles(title *string) []string {
	if title == nil || *title == "" {
		return nil
	}
	return []string{html.UnescapeString(*title)}
}
func BuildLocales(language *string, detected []string) []string {
	primary := "en"
	if language != nil && *language != "" {
		primary = *language
	}
	locales := []string{primary}
	seen := map[string]bool{primary: true}
	for _, locale := range detected {
		if !seen[locale] {
			locales = append(locales, locale)
			seen[locale] = true
		}
	}
	return locales
}
