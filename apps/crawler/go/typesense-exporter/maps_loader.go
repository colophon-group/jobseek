package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sort"

	"github.com/jackc/pgx/v5"
)

type taxonomyQuerier interface {
	Query(context.Context, string, ...any) (pgx.Rows, error)
}

func loadMaps(ctx context.Context, db taxonomyQuerier) (Maps, error) {
	maps := Maps{
		LocationNames:         map[int]map[string]string{},
		LocationFallbackNames: map[int]string{},
		LocationTypes:         map[int]string{},
		LocationAncestors:     map[int][]int{},
		OccupationNames:       map[int]string{},
		OccupationAncestors:   map[int][]int{},
		SeniorityNames:        map[int]string{},
		TechnologyNames:       map[int]string{},
	}
	firstLocationLocale := map[int]string{}
	rows, err := db.Query(ctx, "SELECT location_id, locale, name FROM location_name WHERE is_display = true")
	if err != nil {
		return Maps{}, err
	}
	for rows.Next() {
		var id int
		var locale, name string
		if err := rows.Scan(&id, &locale, &name); err != nil {
			rows.Close()
			return Maps{}, err
		}
		if maps.LocationNames[id] == nil {
			maps.LocationNames[id] = map[string]string{}
			firstLocationLocale[id] = locale
		}
		maps.LocationNames[id][locale] = name
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return Maps{}, err
	}
	rows.Close()
	for id, locale := range firstLocationLocale {
		maps.LocationFallbackNames[id] = maps.LocationNames[id][locale]
	}

	locationParents := map[int]*int{}
	rows, err = db.Query(ctx, "SELECT id, parent_id, type FROM location")
	if err != nil {
		return Maps{}, err
	}
	for rows.Next() {
		var id int
		var parent *int
		var geoType string
		if err := rows.Scan(&id, &parent, &geoType); err != nil {
			rows.Close()
			return Maps{}, err
		}
		locationParents[id], maps.LocationTypes[id] = parent, geoType
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return Maps{}, err
	}
	rows.Close()

	macros := map[int][]int{}
	rows, err = db.Query(ctx, "SELECT country_id, macro_id FROM location_macro_member")
	if err != nil {
		// Match Python's fail-open behavior for this auxiliary seed table.
		slog.Warn("exporter.location_macro_member.unreadable", "error", err)
	} else {
		for rows.Next() {
			var country, macro int
			if err := rows.Scan(&country, &macro); err != nil {
				rows.Close()
				return Maps{}, err
			}
			macros[country] = append(macros[country], macro)
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			return Maps{}, err
		}
		rows.Close()
	}
	if len(macros) == 0 {
		slog.Warn("exporter.location_macro_member.empty")
	}
	for id := range locationParents {
		ancestors, err := locationAncestors(id, locationParents, macros)
		if err != nil {
			return Maps{}, err
		}
		maps.LocationAncestors[id] = ancestors
	}

	occupationParents := map[int]*int{}
	rows, err = db.Query(ctx, "SELECT id, parent_id FROM occupation")
	if err != nil {
		return Maps{}, err
	}
	for rows.Next() {
		var id int
		var parent *int
		if err := rows.Scan(&id, &parent); err != nil {
			rows.Close()
			return Maps{}, err
		}
		occupationParents[id] = parent
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return Maps{}, err
	}
	rows.Close()
	for id := range occupationParents {
		ancestors, err := parentChain(id, occupationParents)
		if err != nil {
			return Maps{}, err
		}
		maps.OccupationAncestors[id] = ancestors
	}
	if err := loadNameMap(ctx, db, "SELECT occupation_id, name FROM occupation_name WHERE locale = 'en' AND is_display = true", maps.OccupationNames); err != nil {
		return Maps{}, err
	}
	if err := loadNameMap(ctx, db, "SELECT seniority_id, name FROM seniority_name WHERE locale = 'en' AND is_display = true", maps.SeniorityNames); err != nil {
		return Maps{}, err
	}
	if err := loadNameMap(ctx, db, "SELECT id, name FROM technology", maps.TechnologyNames); err != nil {
		return Maps{}, err
	}
	return maps, nil
}

func loadNameMap(ctx context.Context, db taxonomyQuerier, query string, target map[int]string) error {
	rows, err := db.Query(ctx, query)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var id int
		var name string
		if err := rows.Scan(&id, &name); err != nil {
			return err
		}
		target[id] = name
	}
	return rows.Err()
}

func parentChain(id int, parents map[int]*int) ([]int, error) {
	result := []int{}
	seen := map[int]struct{}{}
	current := id
	for {
		if _, repeated := seen[current]; repeated {
			return nil, fmt.Errorf("taxonomy parent cycle at %d", current)
		}
		seen[current] = struct{}{}
		result = append(result, current)
		parent, present := parents[current]
		if !present || parent == nil {
			return result, nil
		}
		current = *parent
	}
}

func locationAncestors(id int, parents map[int]*int, macros map[int][]int) ([]int, error) {
	chain, err := parentChain(id, parents)
	if err != nil {
		return nil, err
	}
	all := map[int]struct{}{}
	for _, member := range chain {
		all[member] = struct{}{}
		for _, macro := range macros[member] {
			all[macro] = struct{}{}
		}
	}
	if len(all) == 0 {
		return nil, errors.New("empty location ancestor set")
	}
	result := make([]int, 0, len(all))
	for member := range all {
		result = append(result, member)
	}
	sort.Ints(result) // project() also sorts ancestor-only IDs, as Python does.
	return result, nil
}
