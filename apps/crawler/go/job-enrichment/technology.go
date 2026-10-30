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

// The existing lowercase substring filter admits candidates only. Index its
// exact bytes once instead of rescanning the description for every keyword;
// literalMatch still owns case sensitivity, Unicode folds and word boundaries.
type technologyKeywordNode struct {
	children map[byte]*technologyKeywordNode
	indexes  []int
}

func (m *Matcher) indexTechnologyKeywords() {
	m.technologyKeywords = &technologyKeywordNode{}
	for i, technology := range m.technologies {
		for _, keyword := range technology.keywords {
			node := m.technologyKeywords
			for j := 0; j < len(keyword); j++ {
				if node.children == nil {
					node.children = map[byte]*technologyKeywordNode{}
				}
				if node.children[keyword[j]] == nil {
					node.children[keyword[j]] = &technologyKeywordNode{}
				}
				node = node.children[keyword[j]]
			}
			node.indexes = append(node.indexes, i)
		}
	}
}

func (m *Matcher) technologyCandidates(low string) []bool {
	possible := make([]bool, len(m.technologies))
	for i, technology := range m.technologies {
		possible[i] = len(technology.keywords) == 0
	}
	if m.technologyKeywords == nil {
		return possible
	}
	for _, index := range m.technologyKeywords.indexes {
		possible[index] = true
	}
	for start := 0; start < len(low); start++ {
		node := m.technologyKeywords
		for end := start; end < len(low); end++ {
			node = node.children[low[end]]
			if node == nil {
				break
			}
			for _, index := range node.indexes {
				possible[index] = true
			}
		}
	}
	return possible
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
	m.indexTechnologyKeywords()
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
	possible := m.technologyCandidates(low)
	for i, t := range m.technologies {
		if !possible[i] {
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
