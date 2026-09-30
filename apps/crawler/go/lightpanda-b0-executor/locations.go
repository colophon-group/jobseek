package executor

import (
	"context"
	"database/sql"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

const locationSchema = `
CREATE TABLE entry(id INTEGER PRIMARY KEY,parent_id INTEGER,loc_type TEXT NOT NULL,population INTEGER NOT NULL DEFAULT 0,languages TEXT NOT NULL DEFAULT '');
CREATE TABLE name_index(name TEXT NOT NULL,location_id INTEGER NOT NULL);
CREATE TABLE display_name(location_id INTEGER PRIMARY KEY,name TEXT NOT NULL);
`

// Locations owns its private index and the existing one-connection PostgreSQL
// budget. Mutable resolver state and backfill are serialized across tasks.
type Locations struct {
	mu        sync.Mutex
	store     *Store
	directory string
	index     *sql.DB
	resolver  *enrichment.LocationResolver
	negative  map[string]bool
}

func LoadLocations(ctx context.Context, store *Store) (*Locations, error) {
	if store == nil {
		return nil, errors.New("location database store unavailable")
	}
	directory, err := os.MkdirTemp("", "jobseek-go-locations-")
	if err != nil {
		return nil, err
	}
	l := &Locations{store: store, directory: directory, negative: map[string]bool{}}
	loaded := false
	defer func() {
		if !loaded {
			_ = l.Close()
		}
	}()
	path := filepath.Join(directory, "locations.sqlite3")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := file.Close(); err != nil {
		return nil, err
	}
	dsn := (&url.URL{Scheme: "file", Path: path}).String()
	l.index, err = sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	l.index.SetMaxOpenConns(1)
	if _, err = l.index.ExecContext(ctx, locationSchema); err != nil {
		return nil, err
	}
	if err = l.load(ctx); err != nil {
		return nil, err
	}
	l.resolver, err = enrichment.OpenLocations(path)
	if err != nil {
		return nil, err
	}
	loaded = true
	return l, nil
}

func (l *Locations) Close() error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.resolver != nil {
		_ = l.resolver.Close()
		l.resolver = nil
	}
	if l.index != nil {
		_ = l.index.Close()
		l.index = nil
	}
	if l.directory != "" {
		err := os.RemoveAll(l.directory)
		l.directory = ""
		return err
	}
	return nil
}

func (l *Locations) load(ctx context.Context) error {
	transaction, err := l.index.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer transaction.Rollback()
	rows, err := l.store.pool.Query(ctx, "SELECT id, parent_id, type::text AS type, COALESCE(population, 0) AS population, COALESCE(languages, '{}') AS languages FROM location")
	if err != nil {
		return err
	}
	for rows.Next() {
		var id, population int64
		var parent *int64
		var kind string
		var languages []string
		if err := rows.Scan(&id, &parent, &kind, &population, &languages); err != nil {
			rows.Close()
			return err
		}
		if _, err := transaction.ExecContext(ctx, "INSERT OR REPLACE INTO entry(id,parent_id,loc_type,population,languages) VALUES(?,?,?,?,?)", id, parent, kind, population, strings.Join(languages, ",")); err != nil {
			rows.Close()
			return err
		}
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	rows, err = l.store.pool.Query(ctx, "SELECT location_id, lower(name) AS name FROM location_name WHERE locale = ANY($1)", []string{"en", "de", "fr", "it", "alt", ""})
	if err != nil {
		return err
	}
	type pair struct {
		Name string
		ID   int64
	}
	seen := map[pair]bool{}
	for rows.Next() {
		var id int64
		var name string
		if err := rows.Scan(&id, &name); err != nil {
			rows.Close()
			return err
		}
		for _, variant := range enrichment.LocationNameVariants(name) {
			key := pair{variant, id}
			if seen[key] {
				continue
			}
			seen[key] = true
			if _, err := transaction.ExecContext(ctx, "INSERT INTO name_index(name,location_id) VALUES(?,?)", variant, id); err != nil {
				rows.Close()
				return err
			}
		}
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	rows, err = l.store.pool.Query(ctx, "SELECT location_id, name, COALESCE(is_display, false) AS is_display FROM location_name WHERE locale = 'en'")
	if err != nil {
		return err
	}
	// Preserve first English name, then the last display-marked override in
	// query order, matching the existing two-pass Python loader.
	display := map[int64]string{}
	preferred := map[int64]string{}
	for rows.Next() {
		var id int64
		var name string
		var isDisplay bool
		if err := rows.Scan(&id, &name, &isDisplay); err != nil {
			rows.Close()
			return err
		}
		if _, exists := display[id]; !exists {
			display[id] = name
		}
		if isDisplay {
			preferred[id] = name
		}
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	for id, name := range preferred {
		display[id] = name
	}
	for id, name := range display {
		if _, err := transaction.ExecContext(ctx, "INSERT OR REPLACE INTO display_name(location_id,name) VALUES(?,?)", id, name); err != nil {
			return err
		}
	}
	if _, err := transaction.ExecContext(ctx, "CREATE INDEX idx_name ON name_index(name)"); err != nil {
		return err
	}
	return transaction.Commit()
}

func (l *Locations) Resolve(ctx context.Context, raw []string, fallback, language string) ([]int64, []string, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.resolver == nil {
		return nil, nil, errors.New("location index closed")
	}
	negative := make([]string, 0, len(l.negative))
	for key := range l.negative {
		negative = append(negative, key)
	}
	results, missed, _, err := l.resolver.Resolve(raw, fallback, language, true, negative)
	if err != nil {
		return nil, nil, err
	}
	if changed, err := l.backfill(ctx, missed); err != nil {
		return nil, nil, err
	} else if changed {
		negative = negative[:0]
		for key := range l.negative {
			negative = append(negative, key)
		}
		results, _, _, err = l.resolver.Resolve(raw, fallback, language, true, negative)
		if err != nil {
			return nil, nil, err
		}
	}
	// B0 deliberately discards taxonomy-miss telemetry, as its current lookup
	// provider does. Only resolved IDs become parallel persistence arrays.
	var ids []int64
	var kinds []string
	for _, result := range results {
		if result.ID != nil {
			ids = append(ids, *result.ID)
			kinds = append(kinds, result.Type)
		}
	}
	return ids, kinds, nil
}

func (l *Locations) backfill(ctx context.Context, missed []string) (bool, error) {
	if len(missed) == 0 {
		return false, nil
	}
	// SQL ANY is order independent. Sorting makes chunk boundaries stable;
	// row order within each returned chunk remains PostgreSQL-owned.
	sort.Strings(missed)
	type pair struct {
		Name string
		ID   int64
	}
	var pairs []pair
	matched := map[string]bool{}
	for start := 0; start < len(missed); start += 500 {
		chunk := missed[start:min(start+500, len(missed))]
		rows, err := l.store.pool.Query(ctx, "SELECT location_id, lower(name) AS name FROM location_name WHERE lower(name) = ANY($1::text[])", chunk)
		if err != nil {
			return false, err
		}
		for rows.Next() {
			var id int64
			var name string
			if err := rows.Scan(&id, &name); err != nil {
				rows.Close()
				return false, err
			}
			matched[name] = true
			for _, variant := range enrichment.LocationNameVariants(name) {
				pairs = append(pairs, pair{variant, id})
			}
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			return false, err
		}
	}
	if len(pairs) != 0 {
		transaction, err := l.index.BeginTx(ctx, nil)
		if err != nil {
			return false, err
		}
		defer transaction.Rollback()
		for _, pair := range pairs {
			// This index intentionally has no pair uniqueness constraint, just
			// like Python's schema. Preserve INSERT OR IGNORE behavior verbatim.
			if _, err := transaction.ExecContext(ctx, "INSERT OR IGNORE INTO name_index(name,location_id) VALUES(?,?)", pair.Name, pair.ID); err != nil {
				return false, err
			}
		}
		if err := transaction.Commit(); err != nil {
			return false, err
		}
	}
	for _, key := range missed {
		if !matched[key] {
			l.negative[key] = true
		}
	}
	return len(pairs) != 0, nil
}
