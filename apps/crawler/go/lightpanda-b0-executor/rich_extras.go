package executor

import (
	"encoding/json"
	"errors"
	"regexp"
	"strings"
)

var richExtraTags = regexp.MustCompile(`<[^>]+>`)

func richExtraPlain(text string) string { return pythonTrim(richExtraTags.ReplaceAllString(text, " ")) }

func richExtraMeaningful(text string) bool {
	switch pythonLower(richExtraPlain(text)) {
	case "", "unavailable", "not available", "n/a", "none", "null", "-":
		return false
	}
	return true
}

func richExtraString(value any) (string, error) {
	if value == nil {
		return "None", nil
	}
	if s, ok := value.(string); ok {
		return s, nil
	}
	switch value.(type) {
	case bool, json.Number, int, int64, float64:
		s, err := CoerceText(value)
		if err != nil {
			return "", err
		}
		if s == nil {
			return "", nil
		}
		return *s, nil
	}
	return "", errors.New("unsupported structured description item")
}

// This runs after description normalization and before derived fields/hashing,
// matching job_content.enrich_description. Duplicate checks use the original
// description; structured lists retain their source order and original HTML.
func enrichRichDescription(description *string, extras map[string]any) (*string, error) {
	if len(extras) == 0 {
		return description, nil
	}
	plain := ""
	if description != nil {
		plain = pythonLower(richExtraPlain(*description))
	}
	sections := []string{}
	for _, section := range [][2]string{{"responsibilities", "Responsibilities"}, {"qualifications", "Qualifications"}, {"skills", "Skills"}} {
		value := extras[section[0]]
		if value == nil {
			continue
		}
		if text, ok := value.(string); ok {
			if !richExtraMeaningful(text) {
				continue
			}
			snippet := []rune(pythonLower(richExtraPlain(text)))
			if plain != "" && strings.Contains(plain, string(snippet[:min(80, len(snippet))])) {
				continue
			}
			sections = append(sections, "<h3>"+section[1]+"</h3>\n"+text)
			continue
		}
		items, ok := value.([]any)
		if values, stringsOK := value.([]string); stringsOK {
			items = make([]any, len(values))
			for i, v := range values {
				items[i] = v
			}
			ok = true
		}
		if !ok {
			continue
		} // Python ignores non-string/list extras.
		nonempty := []string{}
		for _, item := range items {
			text, err := richExtraString(item)
			if err != nil {
				return nil, err
			}
			if richExtraMeaningful(text) {
				nonempty = append(nonempty, text)
			}
		}
		if len(nonempty) == 0 {
			continue
		}
		present := false
		for _, text := range nonempty {
			snippet := []rune(pythonLower(richExtraPlain(text)))
			if len(snippet) >= 10 {
				present = plain != "" && strings.Contains(plain, string(snippet[:min(80, len(snippet))]))
				break
			}
		}
		if present {
			continue
		}
		var list strings.Builder
		for _, text := range nonempty {
			list.WriteString("<li>" + text + "</li>")
		}
		sections = append(sections, "<h3>"+section[1]+"</h3>\n<ul>"+list.String()+"</ul>")
	}
	if len(sections) == 0 {
		return description, nil
	}
	text := strings.Join(sections, "\n")
	if description != nil && *description != "" {
		text = *description + "\n" + text
	}
	return &text, nil
}
