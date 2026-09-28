package dom

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"golang.org/x/text/cases"
	"golang.org/x/text/language"
)

type Object = map[string]any

var punctuation = strings.NewReplacer("‘", "'", "’", "'", "“", `"`, "”", `"`, "–", "-", "—", "-", "\u00a0", " ", "\u200b", "", "\u200c", "", "\u200d", "", "\ufeff", "")

func norm(s string) string { return cases.Lower(language.Und).String(punctuation.Replace(s)) }
func truth(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case string:
		return x != ""
	case json.Number:
		return x != "0" && x != "0.0"
	case []any:
		return len(x) > 0
	case Object:
		return len(x) > 0
	}
	return true
}
func text(v any) (string, error) {
	if v == nil {
		return "", nil
	}
	s, ok := v.(string)
	if !ok {
		return "", errors.New("step option must be a string")
	}
	return s, nil
}
func number(v any, defaultValue int) (int, error) {
	if v == nil {
		return defaultValue, nil
	}
	switch x := v.(type) {
	case int:
		return x, nil
	case json.Number:
		n, e := x.Int64()
		return int(n), e
	case bool:
		if x {
			return 1, nil
		}
		return 0, nil
	}
	return 0, errors.New("step cursor/count must be an integer")
}
func attrMatch(attrs map[string]string, rule string) bool {
	key, value, hasValue := strings.Cut(rule, "=")
	stored, exists := attrs[key]
	return exists && (!hasValue || strings.Contains(stored, value))
}
func pythonIndex(elements []Element, i int) (Element, error) {
	if i < 0 {
		i += len(elements)
	}
	if i < 0 || i >= len(elements) {
		return Element{}, errors.New("step cursor out of bounds")
	}
	return elements[i], nil
}

var htmlEscape = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&#x27;")

func joinHTML(elements []Element) string {
	var out strings.Builder
	inList := false
	for _, e := range elements {
		value := htmlEscape.Replace(e.Text)
		if e.Tag == "li" {
			if !inList {
				out.WriteString("<ul>")
				inList = true
			}
			out.WriteString("<li>" + value + "</li>")
		} else {
			if inList {
				out.WriteString("</ul>")
				inList = false
			}
			out.WriteString("<" + e.Tag + ">" + value + "</" + e.Tag + ">")
		}
	}
	if inList {
		out.WriteString("</ul>")
	}
	return out.String()
}
func inputDate(value string, format any) (string, error) {
	f, ok := format.(string)
	if !ok || f == "" || len([]rune(f)) > 64 {
		return "", errors.New("step date_input_format must be a non-empty bounded string")
	}
	directives := map[byte]string{'d': "2", 'm': "1", 'Y': "2006", 'y': "06", 'b': "Jan", 'B': "January", 'a': "Mon", 'A': "Monday", 'H': "15", 'I': "3", 'M': "4", 'S': "5", 'f': "000000", 'p': "PM", 'z': "-0700", 'Z': "MST", 'j': "002"}
	var layout strings.Builder
	for i := 0; i < len(f); i++ {
		if f[i] != '%' {
			layout.WriteByte(f[i])
			continue
		}
		i++
		if i == len(f) {
			return "", errors.New("trailing date format directive")
		}
		if f[i] == '%' {
			layout.WriteByte('%')
			continue
		}
		mapped, ok := directives[f[i]]
		if !ok {
			return "", fmt.Errorf("unsupported date directive %%%c", f[i])
		}
		layout.WriteString(mapped)
	}
	dateLayout := layout.String()
	if !strings.Contains(f, "%Y") && !strings.Contains(f, "%y") {
		dateLayout = "2006 " + dateLayout
		value = "1900 " + value
	}
	parsed, err := time.Parse(dateLayout, value)
	if err != nil {
		return "", errors.New("step value does not match date_input_format")
	}
	year := parsed.Year()
	if !strings.Contains(f, "%Y") && !strings.Contains(f, "%y") {
		year = 1900
	}
	return fmt.Sprintf("%04d-%02d-%02d", year, parsed.Month(), parsed.Day()), nil
}
func postprocess(value string, step Object, cache regexCache) (any, error) {
	if truth(step["regex"]) {
		pattern, err := text(step["regex"])
		if err != nil {
			return nil, err
		}
		value, err = cache.capture(pattern, value)
		if err != nil {
			return nil, err
		}
	}
	if format := step["date_input_format"]; format != nil {
		var err error
		value, err = inputDate(value, format)
		if err != nil {
			return nil, err
		}
	}
	if truth(step["split"]) {
		delimiter, err := text(step["split"])
		if err != nil {
			return nil, err
		}
		out := []string{}
		for _, part := range strings.Split(value, delimiter) {
			if trim(part) != "" {
				out = append(out, part)
			}
		}
		return out, nil
	}
	return value, nil
}

// WalkSteps preserves seek/range/anchor cursors, null fields, punctuation
// matching, HTML/list grouping and postprocessing of the canonical engine.
func WalkSteps(elements []Element, steps []Object, start int) (Object, int, error) {
	result := Object{}
	cursor := start
	cache := regexCache{}
	for _, step := range steps {
		field, err := text(step["field"])
		if err != nil {
			return nil, 0, err
		}
		if field != "" {
			if _, exists := result[field]; !exists {
				result[field] = nil
			}
		}
		seek, err := number(step["from"], cursor)
		if err != nil {
			return nil, 0, err
		}
		matchIndex := 0
		found := false
		for i := seek; i < len(elements); i++ {
			e, err := pythonIndex(elements, i)
			if err != nil {
				return nil, 0, err
			}
			regexMatches := true
			if step["match_regex"] != nil {
				pattern, err := text(step["match_regex"])
				if err != nil {
					return nil, 0, err
				}
				regexMatches, err = cache.matches(pattern, e.Text, true)
				if err != nil {
					return nil, 0, err
				}
			}
			tag, err := text(step["tag"])
			if err != nil {
				return nil, 0, err
			}
			substring, err := text(step["text"])
			if err != nil {
				return nil, 0, err
			}
			attr, err := text(step["attr"])
			if err != nil {
				return nil, 0, err
			}
			if (step["tag"] == nil || e.Tag == tag) && (step["text"] == nil || strings.Contains(norm(e.Text), norm(substring))) && regexMatches && (!truth(step["attr"]) || attrMatch(e.Attrs, attr)) {
				matchIndex = i
				found = true
				break
			}
		}
		if !found {
			continue
		}
		offset, err := number(step["offset"], 0)
		if err != nil {
			return nil, 0, err
		}
		matchIndex = min(matchIndex+offset, len(elements)-1)
		if field == "" {
			cursor = matchIndex + 1
			continue
		}
		isRange := truth(step["stop"]) || truth(step["stop_tag"]) || truth(step["stop_attr"]) || truth(step["stop_regex"]) || truth(step["stop_count"]) || truth(step["to_end"])
		if !isRange {
			e, err := pythonIndex(elements, matchIndex)
			if err != nil {
				return nil, 0, err
			}
			value, err := postprocess(e.Text, step, cache)
			if err != nil {
				return nil, 0, err
			}
			result[field] = value
			cursor = matchIndex + 1
			continue
		}
		collected := []Element{}
		stopIndex := -1
		stopTags := map[string]bool{}
		switch tags := step["stop_tag"].(type) {
		case string:
			stopTags[tags] = true
		case []any:
			for _, tag := range tags {
				t, err := text(tag)
				if err != nil {
					return nil, 0, err
				}
				stopTags[t] = true
			}
		}
		for i := matchIndex; i < len(elements); i++ {
			e, err := pythonIndex(elements, i)
			if err != nil {
				return nil, 0, err
			}
			if i != matchIndex {
				stop, err := text(step["stop"])
				if err != nil {
					return nil, 0, err
				}
				if truth(step["stop"]) && strings.Contains(norm(e.Text), norm(stop)) || stopTags[e.Tag] {
					stopIndex = i
					break
				}
				if truth(step["stop_attr"]) {
					attr, err := text(step["stop_attr"])
					if err != nil {
						return nil, 0, err
					}
					if attrMatch(e.Attrs, attr) {
						stopIndex = i
						break
					}
				}
				if truth(step["stop_regex"]) {
					pattern, err := text(step["stop_regex"])
					if err != nil {
						return nil, 0, err
					}
					matched, err := cache.matches(pattern, e.Text, true)
					if err != nil {
						return nil, 0, err
					}
					if matched {
						stopIndex = i
						break
					}
				}
			}
			if truth(step["stop_count"]) {
				count, err := number(step["stop_count"], 0)
				if err != nil {
					return nil, 0, err
				}
				if len(collected) >= count {
					stopIndex = i
					break
				}
			}
			collected = append(collected, e)
		}
		value := ""
		if truth(step["html"]) {
			value = joinHTML(collected)
		} else {
			parts := []string{}
			for _, e := range collected {
				parts = append(parts, e.Text)
			}
			value = strings.Join(parts, "\n")
		}
		processed, err := postprocess(value, step, cache)
		if err != nil {
			return nil, 0, err
		}
		result[field] = processed
		if stopIndex >= 0 {
			cursor = stopIndex
		} else {
			cursor = matchIndex + len(collected)
		}
	}
	return result, cursor, nil
}
