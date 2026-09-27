package main

import (
	"context"
	"errors"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
	"golang.org/x/text/cases"
	"golang.org/x/text/language"
	"golang.org/x/text/unicode/norm"
)

var occupationParenthesisGender = regexp.MustCompile(`\((?:ne|e|euse)\)`)
var occupationSlashGender = regexp.MustCompile(`/(?:euse|ne|e)`)
var occupationMarker = regexp.MustCompile(`\([hfmwdx/]+\)`)
var occupationTokens = regexp.MustCompile(`[a-z0-9]+`)

func registryWhitespace(r rune) bool { return registryTrim(string(r)) == "" }
func occupationWord(r rune) bool     { return unicode.IsLetter(r) || unicode.IsNumber(r) || r == '_' }
func normalizeRegistryOccupation(raw string) string {
	var clean strings.Builder
	for _, r := range norm.NFKD.String(raw) {
		if norm.NFD.PropertiesString(string(r)).CCC() == 0 {
			clean.WriteRune(r)
		}
	}
	text := registryTrim(cases.Lower(language.Und).String(clean.String()))
	text = occupationParenthesisGender.ReplaceAllString(text, "")
	matches := occupationSlashGender.FindAllStringIndex(text, -1)
	for i := len(matches) - 1; i >= 0; i-- {
		m := matches[i]
		end := m[1]
		if end == len(text) {
			text = text[:m[0]] + text[end:]
			continue
		}
		r, _ := utf8.DecodeRuneInString(text[end:])
		if !occupationWord(r) {
			text = text[:m[0]] + text[end:]
		}
	}
	text = occupationMarker.ReplaceAllString(text, "")
	return strings.Join(strings.FieldsFunc(text, registryWhitespace), " ")
}

type registryOccupationAlias struct {
	Alias, Slug string
	Length      int
	Tokens      map[string]bool
}
type registryOccupationResolver struct {
	Ordered []registryOccupationAlias
	Exact   map[string]string
}

func newRegistryOccupationResolver(table registryTable) registryOccupationResolver {
	resolver := registryOccupationResolver{Exact: map[string]string{}}
	indices := map[string]int{}
	add := func(raw, slug string) {
		alias := normalizeRegistryOccupation(raw)
		resolver.Exact[alias] = slug
		if i, exists := indices[alias]; exists {
			resolver.Ordered[i].Slug = slug
			return
		}
		tokens := map[string]bool{}
		for _, token := range occupationTokens.FindAllString(alias, -1) {
			tokens[token] = true
		}
		indices[alias] = len(resolver.Ordered)
		resolver.Ordered = append(resolver.Ordered, registryOccupationAlias{alias, slug, utf8.RuneCountInString(alias), tokens})
	}
	for _, row := range table.Rows {
		slug := registryText(row, "slug")
		add(strings.ReplaceAll(slug, "-", " "), slug)
		for _, locale := range table.Columns {
			switch locale {
			case "slug", "parent", "domain", "aliases":
				continue
			}
			if name := registryText(row, locale); name != "" {
				add(name, slug)
			}
		}
		for _, alias := range strings.Split(registryText(row, "aliases"), "|") {
			if alias = registryTrim(alias); alias != "" {
				add(alias, slug)
			}
		}
	}
	return resolver
}
func occupationBoundary(alias, text string) bool {
	if alias == "" {
		return false
	}
	for start := 0; start <= len(text); {
		i := strings.Index(text[start:], alias)
		if i < 0 {
			return false
		}
		i += start
		end := i + len(alias)
		left, right := i == 0, end == len(text)
		if !left {
			r, _ := utf8.DecodeLastRuneInString(text[:i])
			left = registryWhitespace(r) || strings.ContainsRune(",/-(", r)
		}
		if !right {
			r, _ := utf8.DecodeRuneInString(text[end:])
			right = registryWhitespace(r) || strings.ContainsRune(",:/-)", r)
		}
		if left && right {
			return true
		}
		start = i + 1
	}
	return false
}
func (r registryOccupationResolver) Match(raw string) string {
	if raw == "" {
		return ""
	}
	text := normalizeRegistryOccupation(raw)
	if slug, exists := r.Exact[text]; exists {
		return slug
	}
	best, bestLength := "", 0
	for _, alias := range r.Ordered {
		if alias.Length > bestLength && occupationBoundary(alias.Alias, text) {
			best = alias.Slug
			bestLength = alias.Length
		}
	}
	if best != "" {
		return best
	}
	tokens := map[string]bool{}
	for _, token := range occupationTokens.FindAllString(text, -1) {
		tokens[token] = true
	}
	bestCount := 0
	for _, alias := range r.Ordered {
		if len(alias.Tokens) < 4 || len(alias.Tokens) <= bestCount {
			continue
		}
		contained := true
		for token := range alias.Tokens {
			if !tokens[token] {
				contained = false
				break
			}
		}
		if contained {
			best = alias.Slug
			bestCount = len(alias.Tokens)
		}
	}
	return best
}

func resolveRegistryMisses(ctx context.Context, tx pgx.Tx, tables map[string]registryTable) (int, error) {
	rows, err := tx.Query(ctx, "SELECT id, taxonomy, raw_value FROM taxonomy_miss WHERE status = 'pending'")
	if err != nil {
		return 0, err
	}
	type miss struct {
		ID            int
		Taxonomy, Raw string
	}
	pending := []miss{}
	for rows.Next() {
		var m miss
		if err = rows.Scan(&m.ID, &m.Taxonomy, &m.Raw); err != nil {
			rows.Close()
			return 0, err
		}
		pending = append(pending, m)
	}
	err = rows.Err()
	rows.Close()
	if err != nil || len(pending) == 0 {
		return 0, err
	}
	ids := map[string]map[string]int{}
	for _, kind := range []string{"occupation", "seniority", "technology"} {
		rows, err = tx.Query(ctx, "SELECT id, slug FROM "+kind)
		if err != nil {
			return 0, err
		}
		ids[kind] = map[string]int{}
		for rows.Next() {
			var id int
			var slug string
			if err = rows.Scan(&id, &slug); err != nil {
				rows.Close()
				return 0, err
			}
			ids[kind][slug] = id
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return 0, err
		}
	}
	resolver := newRegistryOccupationResolver(tables["occupations"])
	technologyNames := map[string]string{}
	for _, row := range tables["technologies"].Rows {
		if name := registryText(row, "name"); name != "" {
			technologyNames[cases.Lower(language.Und).String(registryTrim(name))] = registryText(row, "slug")
		}
	}
	count := 0
	for _, m := range pending {
		slug, query := "", ""
		switch m.Taxonomy {
		case "occupation":
			if m.Raw != "" && len(tables["occupations"].Columns) == 0 {
				return count, errors.New("occupation CSV required to resolve pending occupation misses")
			}
			slug = resolver.Match(m.Raw)
			query = `UPDATE job_posting SET occupation_id=$1 WHERE lower(enrichment->>'occupation')=$2 AND occupation_id IS NULL`
		case "seniority":
			slug = m.Raw
			query = `UPDATE job_posting SET seniority_id=$1 WHERE enrichment->>'seniority'=$2 AND seniority_id IS NULL`
		case "technology":
			slug = technologyNames[m.Raw]
			query = `UPDATE job_posting SET technology_ids=array_append(technology_ids,$1::int) WHERE id IN (SELECT jp.id FROM job_posting jp,jsonb_array_elements_text(jp.enrichment->'technologies') AS t WHERE lower(t)=$2 AND (jp.technology_ids IS NULL OR NOT jp.technology_ids @> ARRAY[$1::int]))`
		}
		id, exists := ids[m.Taxonomy][slug]
		if slug == "" || !exists {
			continue
		}
		if _, err = tx.Exec(ctx, query, id, m.Raw); err != nil {
			return count, err
		}
		if _, err = tx.Exec(ctx, "UPDATE taxonomy_miss SET status = 'resolved', resolved_to = $1 WHERE id = $2", slug, m.ID); err != nil {
			return count, err
		}
		count++
	}
	return count, nil
}
