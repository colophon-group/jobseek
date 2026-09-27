package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
)

type taxonomyLocationRow struct {
	ID         int      `json:"id"`
	Type       string   `json:"type"`
	Lat        *float64 `json:"lat"`
	Lng        *float64 `json:"lng"`
	Slug       string   `json:"slug"`
	Population *int64   `json:"population"`
	Parent     *int     `json:"parent_id"`
}
type taxonomyNameRow struct {
	LocationID int    `json:"location_id"`
	DomainID   int    `json:"domain_id"`
	IndustryID int    `json:"industry_id"`
	Locale     string `json:"locale"`
	Name       string `json:"name"`
	Display    bool   `json:"is_display"`
}
type taxonomyMacroRow struct {
	MacroID   int `json:"macro_id"`
	CountryID int `json:"country_id"`
}
type taxonomyNamedRow struct {
	ID         int    `json:"id"`
	Slug       string `json:"slug"`
	Parent     *int   `json:"parent_id"`
	Domain     *int   `json:"domain_id"`
	DomainSlug string `json:"domain_slug"`
	Locale     string `json:"locale"`
	Name       string `json:"name"`
	Display    bool   `json:"is_display"`
	Category   string `json:"category"`
}
type taxonomyCompanyRow struct {
	ID           string `json:"id"`
	Industry     *int   `json:"industry"`
	IndustryName string `json:"industry_name"`
}
type taxonomyInputs struct {
	Locations     []taxonomyLocationRow `json:"location_rows"`
	LocationNames []taxonomyNameRow     `json:"location_names"`
	Macros        []taxonomyMacroRow    `json:"location_macros"`
	Occupations   []taxonomyNamedRow    `json:"occupation_rows"`
	DomainNames   []taxonomyNameRow     `json:"occupation_domain_names"`
	Seniorities   []taxonomyNamedRow    `json:"seniority_rows"`
	Technologies  []taxonomyNamedRow    `json:"technology_rows"`
	Companies     []taxonomyCompanyRow  `json:"company_rows"`
	IndustryNames []taxonomyNameRow     `json:"industry_names"`
}
type taxonomyDocuments map[string][]map[string]any

func readTaxonomyRows[T any](ctx context.Context, tx pgx.Tx, query string) ([]T, error) {
	rows, err := tx.Query(ctx, "SELECT row_to_json(t) FROM ("+query+") t")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []T{}
	for rows.Next() {
		var raw []byte
		var row T
		if err := rows.Scan(&raw); err != nil {
			return nil, err
		}
		if err := json.Unmarshal(raw, &row); err != nil {
			return nil, err
		}
		result = append(result, row)
	}
	return result, rows.Err()
}

func loadTaxonomySnapshot(ctx context.Context, conn *pgx.Conn, contract taxonomyContract) (taxonomyDocuments, error) {
	tx, err := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(context.Background())
	input, err := loadTaxonomyInputs(ctx, tx, contract)
	if err != nil {
		return nil, err
	}
	result, err := buildTaxonomyDocuments(input, contract)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(ctx); err != nil {
		return nil, err
	}
	return result, nil
}

func loadTaxonomyInputs(ctx context.Context, tx pgx.Tx, contract taxonomyContract) (taxonomyInputs, error) {
	var input taxonomyInputs
	var err error
	if input.Locations, err = readTaxonomyRows[taxonomyLocationRow](ctx, tx, contract.Queries["location_rows"]); err != nil {
		return input, err
	}
	if input.LocationNames, err = readTaxonomyRows[taxonomyNameRow](ctx, tx, contract.Queries["location_names"]); err != nil {
		return input, err
	}
	if input.Macros, err = readTaxonomyRows[taxonomyMacroRow](ctx, tx, contract.Queries["location_macros"]); err != nil {
		return input, err
	}
	if input.Occupations, err = readTaxonomyRows[taxonomyNamedRow](ctx, tx, contract.Queries["occupation_rows"]); err != nil {
		return input, err
	}
	if input.DomainNames, err = readTaxonomyRows[taxonomyNameRow](ctx, tx, contract.Queries["occupation_domain_names"]); err != nil {
		return input, err
	}
	if input.Seniorities, err = readTaxonomyRows[taxonomyNamedRow](ctx, tx, contract.Queries["seniority_rows"]); err != nil {
		return input, err
	}
	if input.Technologies, err = readTaxonomyRows[taxonomyNamedRow](ctx, tx, contract.Queries["technology_rows"]); err != nil {
		return input, err
	}
	if input.Companies, err = readTaxonomyRows[taxonomyCompanyRow](ctx, tx, contract.Queries["company_rows"]); err != nil {
		return input, err
	}
	if input.IndustryNames, err = readTaxonomyRows[taxonomyNameRow](ctx, tx, contract.Queries["industry_names"]); err != nil {
		return input, err
	}
	return input, nil
}

func taxonomySlug(raw string) (string, error) {
	slug := strings.TrimSpace(raw)
	if slug == "" {
		return "", errors.New("authoritative taxonomy has a blank slug")
	}
	return slug, nil
}
func taxonomyDisplay(names map[int]map[string]string, id int, locale, name string) error {
	if names[id] == nil {
		names[id] = map[string]string{}
	}
	if old, exists := names[id][locale]; exists && old != name {
		return errors.New("authoritative taxonomy has conflicting display names")
	}
	names[id][locale] = name
	return nil
}
func taxonomySortedInts(values map[int]bool) []int {
	result := make([]int, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Ints(result)
	return result
}
func taxonomySortedStrings(values map[string]bool) []string {
	result := make([]string, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}

func buildTaxonomyLocations(input taxonomyInputs, contract taxonomyContract) ([]map[string]any, error) {
	names := map[int]map[string]string{}
	aliases := map[int]map[string]bool{}
	for _, row := range input.LocationNames {
		if row.Display {
			if err := taxonomyDisplay(names, row.LocationID, row.Locale, row.Name); err != nil {
				return nil, err
			}
		} else {
			if aliases[row.LocationID] == nil {
				aliases[row.LocationID] = map[string]bool{}
			}
			aliases[row.LocationID][row.Name] = true
		}
	}
	locations := map[int]taxonomyLocationRow{}
	macros := map[int]map[int]bool{}
	members := map[int]map[int]bool{}
	for _, row := range input.Locations {
		if row.Type == "" {
			row.Type = "city"
		}
		locations[row.ID] = row
	}
	for _, row := range input.Macros {
		if macros[row.CountryID] == nil {
			macros[row.CountryID] = map[int]bool{}
		}
		macros[row.CountryID][row.MacroID] = true
		if members[row.MacroID] == nil {
			members[row.MacroID] = map[int]bool{}
		}
		members[row.MacroID][row.CountryID] = true
	}
	documents := []map[string]any{}
	for _, row := range input.Locations {
		id := row.ID
		ancestors := map[int]bool{}
		path := map[int]bool{}
		current := &id
		for current != nil {
			if path[*current] {
				return nil, errors.New("authoritative location hierarchy contains a cycle")
			}
			ancestor, ok := locations[*current]
			if !ok {
				return nil, errors.New("authoritative location hierarchy references a missing parent")
			}
			path[*current] = true
			ancestors[*current] = true
			if ancestor.Type == "country" {
				for macro := range macros[*current] {
					ancestors[macro] = true
				}
			}
			current = ancestor.Parent
		}
		delete(ancestors, id)
		ancestorIDs := append([]int{id}, taxonomySortedInts(ancestors)...)
		name := strings.TrimSpace(names[id]["en"])
		if name == "" {
			return nil, errors.New("authoritative location has no English display name")
		}
		slug, err := taxonomySlug(row.Slug)
		if err != nil {
			return nil, err
		}
		doc := map[string]any{"id": strconv.Itoa(id), "location_id": id, "slug": slug, "name_en": name, "type": locations[id].Type, "ancestor_ids": ancestorIDs}
		for _, locale := range []string{"de", "fr", "it"} {
			if name := names[id][locale]; name != "" {
				doc["name_"+locale] = name
			}
		}
		if row.Lat != nil && row.Lng != nil {
			doc["coordinates"] = []float64{*row.Lat, *row.Lng}
		}
		if row.Parent != nil {
			doc["parent_id"] = *row.Parent
			if parentName := names[*row.Parent]["en"]; parentName != "" {
				doc["parent_name"] = parentName
			}
		}
		if len(members[id]) > 0 {
			doc["member_country_ids"] = taxonomySortedInts(members[id])
		}
		if row.Population != nil {
			doc["population"] = *row.Population
		}
		allAliases := map[string]bool{}
		for alias := range aliases[id] {
			allAliases[alias] = true
		}
		if locations[id].Type == "macro" {
			for _, alias := range contract.MacroAliases[slug] {
				allAliases[alias] = true
			}
		}
		if len(allAliases) > 0 {
			doc["aliases"] = taxonomySortedStrings(allAliases)
		}
		documents = append(documents, doc)
	}
	return documents, nil
}

func buildTaxonomyNamed(rows []taxonomyNamedRow, domainRows []taxonomyNameRow, kind string) ([]map[string]any, error) {
	type groupKey struct {
		id     int
		locale string
	}
	type group struct {
		row     taxonomyNamedRow
		name    *string
		aliases []string
	}
	domains := map[int]map[string]string{}
	for _, row := range domainRows {
		if err := taxonomyDisplay(domains, row.DomainID, row.Locale, row.Name); err != nil {
			return nil, err
		}
	}
	groups := map[groupKey]*group{}
	wildcards := map[int][]string{}
	for _, row := range rows {
		slug, err := taxonomySlug(row.Slug)
		if err != nil {
			return nil, err
		}
		row.Slug = slug
		if row.Locale == "*" {
			wildcards[row.ID] = append(wildcards[row.ID], row.Name)
		}
		key := groupKey{row.ID, row.Locale}
		g := groups[key]
		if g == nil {
			g = &group{row: row, aliases: []string{}}
			groups[key] = g
		}
		if row.Display {
			if g.name != nil && *g.name != row.Name {
				return nil, errors.New("authoritative taxonomy has conflicting display names")
			}
			name := row.Name
			g.name = &name
		} else {
			g.aliases = append(g.aliases, row.Name)
		}
	}
	keys := make([]groupKey, 0, len(groups))
	for key := range groups {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].id == keys[j].id {
			return keys[i].locale < keys[j].locale
		}
		return keys[i].id < keys[j].id
	})
	docs := []map[string]any{}
	for _, key := range keys {
		g := groups[key]
		if key.locale == "*" || g.name == nil || *g.name == "" {
			continue
		}
		aliases := append(append([]string{}, g.aliases...), wildcards[key.id]...)
		sort.Strings(aliases)
		doc := map[string]any{"id": fmt.Sprintf("%d-%s", key.id, key.locale), kind + "_id": key.id, "slug": g.row.Slug, "name": *g.name, "aliases": aliases, "locale": key.locale}
		if kind == "occupation" {
			if g.row.Parent != nil {
				doc["parent_id"] = *g.row.Parent
			}
			if g.row.Domain != nil {
				id := *g.row.Domain
				doc["domain_id"] = id
				if g.row.DomainSlug != "" {
					doc["domain_slug"] = g.row.DomainSlug
				}
				name := domains[id][key.locale]
				if name == "" {
					name = domains[id]["en"]
				}
				if name != "" {
					doc["domain_name"] = name
				}
			}
		}
		docs = append(docs, doc)
	}
	return docs, nil
}

func buildTaxonomyDocuments(input taxonomyInputs, contract taxonomyContract) (taxonomyDocuments, error) {
	result := taxonomyDocuments{}
	var err error
	if result["location"], err = buildTaxonomyLocations(input, contract); err != nil {
		return nil, err
	}
	if result["occupation"], err = buildTaxonomyNamed(input.Occupations, input.DomainNames, "occupation"); err != nil {
		return nil, err
	}
	if result["seniority"], err = buildTaxonomyNamed(input.Seniorities, nil, "seniority"); err != nil {
		return nil, err
	}
	result["technology"] = []map[string]any{}
	for _, row := range input.Technologies {
		slug, err := taxonomySlug(row.Slug)
		if err != nil {
			return nil, err
		}
		name := row.Name
		if name == "" {
			name = slug
		}
		doc := map[string]any{"id": strconv.Itoa(row.ID), "technology_id": row.ID, "slug": slug, "name": name}
		if row.Category != "" {
			doc["category"] = row.Category
		}
		result["technology"] = append(result["technology"], doc)
	}
	industries := map[int]map[string]string{}
	for _, row := range input.IndustryNames {
		if err := taxonomyDisplay(industries, row.IndustryID, row.Locale, row.Name); err != nil {
			return nil, err
		}
	}
	result["company"] = []map[string]any{}
	for _, row := range input.Companies {
		doc := map[string]any{"id": row.ID}
		if row.Industry != nil {
			id := *row.Industry
			doc["industry_id"] = id
			if row.IndustryName != "" {
				doc["industry_name"] = row.IndustryName
			}
			for _, locale := range []string{"de", "fr", "it"} {
				if name := industries[id][locale]; name != "" {
					doc["industry_name_"+locale] = name
				}
			}
		}
		result["company"] = append(result["company"], doc)
	}
	return result, nil
}
