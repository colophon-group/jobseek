package enrichment

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"
)

//go:embed experience_patterns.json
var experiencePatternsJSON []byte
var experiencePatterns = map[string]string{}
var experienceRules []*regexp.Regexp
var experienceFalsePositive, experienceMonth, experiencePrefix *regexp.Regexp
var experienceTags = regexp.MustCompile(`<[^>]+>`)
var experienceSpaces = regexp.MustCompile(`[ \t]+`)

func experienceRegex(pattern string) *regexp.Regexp {
	// RE2 lacks lookbehind; check this one numeric boundary at the captured
	// minimum below. Preserve Python's Unicode digit, word and space classes.
	pattern = strings.ReplaceAll(pattern, `(?<![\d.])`, "")
	pattern = strings.ReplaceAll(pattern, `[\w]`, `[\p{L}\p{N}_]`)
	pattern = strings.ReplaceAll(pattern, `\d`, `\p{Nd}`)
	pattern = strings.ReplaceAll(pattern, `\s`, `[\t\n\v\f\r \x{001c}-\x{001f}\x{0085}\x{00a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}]`)
	return regexp.MustCompile("(?i)" + pattern)
}
func init() {
	if err := json.Unmarshal(experiencePatternsJSON, &experiencePatterns); err != nil {
		panic(err)
	}
	for i, name := range []string{"_EXPERIENCE_MIXED_RANGE_RE", "_EXPERIENCE_RE", "_EXPERIENCE_REVERSED_RE"} {
		pattern := experiencePatterns[name]
		if i < 2 {
			pattern = "^(?:" + pattern + ")"
		}
		experienceRules = append(experienceRules, experienceRegex(pattern))
	}
	experiencePrefix = experienceRegex(strings.TrimSuffix(strings.Split(experiencePatterns["_EXPERIENCE_RE"], `(?<![\d.])`)[0], "?"))
	experienceFalsePositive = experienceRegex(experiencePatterns["_FALSE_POSITIVE_RE"])
	experienceMonth = experienceRegex("^(?:" + experiencePatterns["_MONTH_UNIT_RE"] + ")$")
}
func decimalDigit(r rune) int {
	if r >= '0' && r <= '9' {
		return int(r - '0')
	}
	for _, t := range unicode.Nd.R16 {
		if uint32(r) >= uint32(t.Lo) && uint32(r) <= uint32(t.Hi) && (uint32(r)-uint32(t.Lo))%uint32(t.Stride) == 0 {
			return int((uint32(r)-uint32(t.Lo))/uint32(t.Stride)) % 10
		}
	}
	for _, t := range unicode.Nd.R32 {
		if uint32(r) >= t.Lo && uint32(r) <= t.Hi && (uint32(r)-t.Lo)%t.Stride == 0 {
			return int((uint32(r)-t.Lo)/t.Stride) % 10
		}
	}
	panic(fmt.Sprintf("invalid decimal digit %U", r))
}
func experienceTenths(number, unit string) int {
	value := 0
	fractional := false
	for _, r := range number {
		if r == '.' || r == ',' {
			fractional = true
			continue
		}
		value = value*10 + decimalDigit(r)
	}
	if !fractional {
		value *= 10
	}
	if experienceMonth.MatchString(unit) {
		value = (value + 6) / 12
	}
	return value
}

// Experience returns the highest accepted minimum. Equal minima preserve
// mixed-range / forward / reversed rule order, including a null maximum.
func Experience(raw string) (*float64, *float64) {
	text := strings.TrimFunc(experienceSpaces.ReplaceAllString(experienceTags.ReplaceAllString(raw, " "), " "), space)
	// Python's Unicode IGNORECASE equates all four I forms. The remaining
	// simple folds are shared by Go. Rune count is unchanged for context limits.
	text = strings.NewReplacer("İ", "i", "ı", "i").Replace(text)

	// Forward patterns can start only at a numeric boundary (or its optional
	// prefix). Anchor them at those candidates instead of running the large
	// multilingual expression at every character of the description.
	digits := []int{}
	prior := rune(0)
	for at, r := range text {
		if unicode.IsDigit(r) && !unicode.IsDigit(prior) && prior != '.' {
			digits = append(digits, at)
		}
		prior = r
	}
	if len(digits) == 0 {
		return nil, nil
	}
	prefixes := map[int]int{}
	for _, p := range experiencePrefix.FindAllStringIndex(text, -1) {
		prefixes[p[1]] = p[0]
	}
	bestMin, bestMax := -1, -1
	for ruleIndex, rule := range experienceRules {
		for from := 0; from < len(text); {
			searchFrom := from
			var indexes []int
			if ruleIndex < 2 {
				for _, at := range digits[sort.SearchInts(digits, from):] {
					indexes = rule.FindStringSubmatchIndex(text[at:])
					if indexes != nil {
						searchFrom = at
						break
					}
				}
			} else {
				indexes = rule.FindStringSubmatchIndex(text[from:])
			}
			if indexes == nil {
				break
			}
			for i, v := range indexes {
				if v >= 0 {
					indexes[i] += searchFrom
				}
			}
			if ruleIndex < 2 {
				if start, ok := prefixes[searchFrom]; ok && start >= from {
					indexes[0] = start
				}
			}
			start, end := indexes[0], indexes[1]
			minIndex := rule.SubexpIndex("min") * 2
			minStart := indexes[minIndex]
			if minStart > 0 {
				prior, _ := utf8.DecodeLastRuneInString(text[:minStart])
				if prior == '.' || unicode.IsDigit(prior) {
					_, width := utf8.DecodeRuneInString(text[start:])
					from = start + width
					continue
				}
			}
			from = end
			group := func(name string) string {
				i := rule.SubexpIndex(name) * 2
				if i < 0 || indexes[i] < 0 {
					return ""
				}
				return text[indexes[i]:indexes[i+1]]
			}
			unit := group("unit")
			if ruleIndex == 0 {
				unit = group("min_unit")
			}
			min := experienceTenths(group("min"), unit)
			max := -1
			if n := group("max"); n != "" {
				if ruleIndex == 0 {
					unit = group("max_unit")
				}
				max = experienceTenths(n, unit)
			}
			if min > 300 || max > 300 || (max >= 0 && max < min) {
				continue
			}
			contextStart := start
			for count := 0; count < 60 && contextStart > 0; count++ {
				_, size := utf8.DecodeLastRuneInString(text[:contextStart])
				contextStart -= size
			}
			if experienceFalsePositive.MatchString(text[contextStart:start]) {
				continue
			}
			if min > bestMin {
				bestMin, bestMax = min, max
			}
		}
	}
	if bestMin < 0 {
		return nil, nil
	}
	min := float64(bestMin) / 10
	if bestMax < 0 {
		return &min, nil
	}
	max := float64(bestMax) / 10
	return &min, &max
}
