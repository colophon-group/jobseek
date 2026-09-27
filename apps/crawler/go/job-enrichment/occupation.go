package enrichment

import (
	"encoding/csv"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"
)

type alias struct {
	text, slug string
	tokens     map[string]bool
	length     int
}
type trie struct {
	children map[rune]*trie
	terminal int
}
type Matcher struct {
	aliases      []alias
	exact        map[string]int
	root         *trie
	technologies []technology
}

func table(path string) ([]map[string]string, []string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, nil, err
	}
	defer f.Close()
	r := csv.NewReader(io.LimitReader(f, 32<<20))
	headers, err := r.Read()
	if err != nil {
		return nil, nil, err
	}
	rows := []map[string]string{}
	for {
		row, err := r.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, nil, err
		}
		m := map[string]string{}
		for i, k := range headers {
			m[k] = row[i]
		}
		rows = append(rows, m)
	}
	return rows, headers, nil
}
func Load(dataDir string) (*Matcher, error) {
	rows, headers, err := table(filepath.Join(dataDir, "occupations.csv"))
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, fmt.Errorf("occupation taxonomy is empty")
	}
	m := &Matcher{exact: map[string]int{}, root: &trie{terminal: -1}}
	add := func(text, slug string) {
		text = Normalize(text)
		if i, ok := m.exact[text]; ok {
			m.aliases[i].slug = slug
			return
		}
		m.exact[text] = len(m.aliases)
		m.aliases = append(m.aliases, alias{text: text, slug: slug, length: utf8.RuneCountInString(text), tokens: tokenSet(text)})
	}
	for _, row := range rows {
		slug := row["slug"]
		if slug == "" {
			return nil, fmt.Errorf("occupation lacks slug")
		}
		add(strings.ReplaceAll(slug, "-", " "), slug)
		for _, column := range headers {
			switch column {
			case "slug", "parent", "domain", "aliases":
				continue
			}
			if value := row[column]; value != "" {
				add(value, slug)
			}
		}
		for _, value := range strings.Split(row["aliases"], "|") {
			if value = strings.TrimFunc(value, space); value != "" {
				add(value, slug)
			}
		}
	}
	for i, a := range m.aliases {
		t := m.root
		for _, r := range a.text {
			if t.children == nil {
				t.children = map[rune]*trie{}
			}
			if t.children[r] == nil {
				t.children[r] = &trie{terminal: -1}
			}
			t = t.children[r]
		}
		t.terminal = i
	}
	if err = m.loadTechnologies(filepath.Join(dataDir, "technologies.csv")); err != nil {
		return nil, err
	}
	return m, nil
}
func leftBoundary(r rune) bool  { return space(r) || strings.ContainsRune(",/-(", r) }
func rightBoundary(r rune) bool { return space(r) || strings.ContainsRune(",:/-)", r) }
func (m *Matcher) Occupation(raw string) *string {
	if raw == "" {
		return nil
	}
	s := Normalize(raw)
	if i, ok := m.exact[s]; ok {
		v := m.aliases[i].slug
		return &v
	}
	text := []rune(s)
	best := -1
	bestLen := 0
	for start := 0; start < len(text); start++ {
		if start > 0 && !leftBoundary(text[start-1]) {
			continue
		}
		node := m.root
		for end := start; end < len(text); end++ {
			node = node.children[text[end]]
			if node == nil {
				break
			}
			i := node.terminal
			if i < 0 || end+1 < len(text) && !rightBoundary(text[end+1]) {
				continue
			}
			a := m.aliases[i]
			if a.length > bestLen || a.length == bestLen && a.length > 0 && (best < 0 || i < best) {
				best = i
				bestLen = a.length
			}
		}
	}
	if best >= 0 {
		v := m.aliases[best].slug
		return &v
	}
	tokens := tokenSet(s)
	count := 0
	for i, a := range m.aliases {
		if len(a.tokens) < 4 || len(a.tokens) <= count {
			continue
		}
		subset := true
		for token := range a.tokens {
			if !tokens[token] {
				subset = false
				break
			}
		}
		if subset {
			best = i
			count = len(a.tokens)
		}
	}
	if best >= 0 {
		v := m.aliases[best].slug
		return &v
	}
	return nil
}
