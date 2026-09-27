package enrichment

import (
	"strings"
	"unicode"

	"golang.org/x/net/html"
)

type technology struct {
	slug         string
	alternatives []string
	keywords     []string
	sensitive    bool
}

func (m *Matcher) loadTechnologies(path string) error {
	rows, _, err := table(path)
	if err != nil {
		return err
	}
	keywords := map[string][]string{}
	for _, row := range rows {
		alts := []string{}
		for _, v := range strings.Split(row["patterns"], "|") {
			v = strings.TrimFunc(v, space)
			if v != "" {
				alts = append(alts, v)
			}
		}
		if len(alts) == 0 {
			continue
		}
		t := technology{slug: row["slug"], alternatives: alts, sensitive: strings.Contains(row["flags"], "cs")}
		lows := []string{}
		for _, s := range alts {
			lows = append(lows, lower(s))
		}
		keywords[t.slug] = lows
		m.technologies = append(m.technologies, t)
	}
	for i := range m.technologies {
		m.technologies[i].keywords = keywords[m.technologies[i].slug]
	}
	return nil
}
func stripHTML(s string) string {
	z := html.NewTokenizer(strings.NewReader(s))
	parts := []string{}
	for {
		switch z.Next() {
		case html.ErrorToken:
			return strings.Join(parts, " ")
		case html.TextToken:
			parts = append(parts, string(z.Text()))
		}
	}
}
func equalFold(a, b rune) bool {
	if a == b {
		return true
	}
	// Python re.IGNORECASE includes the dotted and dotless I with ASCII I/i.
	if strings.ContainsRune("iIıİ", a) && strings.ContainsRune("iIıİ", b) {
		return true
	}
	for r := unicode.SimpleFold(a); r != a; r = unicode.SimpleFold(r) {
		if r == b {
			return true
		}
	}
	return false
}
func literalMatch(text, pattern []rune, sensitive bool) bool {
	for i := 0; i+len(pattern) <= len(text); i++ {
		if i > 0 && word(text[i-1]) {
			continue
		}
		end := i + len(pattern)
		if end < len(text) && word(text[end]) {
			continue
		}
		ok := true
		for j, r := range pattern {
			if sensitive && text[i+j] != r || !sensitive && !equalFold(text[i+j], r) {
				ok = false
				break
			}
		}
		if ok {
			return true
		}
	}
	return false
}
func (m *Matcher) Technologies(raw string) []string {
	matched := []string{}
	if raw == "" {
		return matched
	}
	if strings.Contains(raw, "<") {
		raw = stripHTML(raw)
	}
	low := lower(raw)
	text := []rune(raw)
	for _, t := range m.technologies {
		possible := len(t.keywords) == 0
		for _, kw := range t.keywords {
			if strings.Contains(low, kw) {
				possible = true
				break
			}
		}
		if !possible {
			continue
		}
		for _, alt := range t.alternatives {
			if literalMatch(text, []rune(alt), t.sensitive) {
				matched = append(matched, t.slug)
				break
			}
		}
	}
	return matched
}
