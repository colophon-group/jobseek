package executor

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

// An explicitly supplied private, read-only taxonomy export exercises the real
// production startup row set in an isolated fixture. No production connection
// or job content is used, and names/slugs never enter diagnostics.
func TestPostgresNativePrivateTaxonomySnapshot(t *testing.T) {
	path := os.Getenv("JOBSEEK_B0_EXECUTOR_TAXONOMY_SNAPSHOT")
	if path == "" {
		t.Skip("private startup-taxonomy snapshot not supplied")
	}
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm() != 0o600 || info.Size() > 64*1024*1024 {
		t.Fatal("private snapshot metadata invalid")
	}
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal("private snapshot unavailable")
	}
	type location struct {
		ID         int64
		Parent     *int64 `json:"parent_id"`
		Type       string
		Population *int64
		Languages  []string
	}
	type name struct {
		ID      int64 `json:"location_id"`
		Locale  string
		Name    string
		Display *bool `json:"is_display"`
	}
	type lookup struct {
		ID   int64
		Slug string
	}
	type rate struct {
		Currency string
		ToEUR    float64 `json:"to_eur"`
	}
	var snapshot struct {
		Locations    []location `json:"location"`
		Names        []name     `json:"location_name"`
		Technologies []lookup   `json:"technology"`
		Occupations  []lookup   `json:"occupation"`
		Seniorities  []lookup   `json:"seniority"`
		Rates        []rate     `json:"currency_rate"`
		Counts       struct {
			Locations int `json:"location"`
			Names     int `json:"location_name"`
		} `json:"counts"`
	}
	if json.Unmarshal(body, &snapshot) != nil || len(snapshot.Locations) != snapshot.Counts.Locations || len(snapshot.Names) != snapshot.Counts.Names || len(snapshot.Locations) < 1 || len(snapshot.Locations) > 100000 || len(snapshot.Names) < 1 || len(snapshot.Names) > 250000 {
		t.Fatal("bounded startup snapshot is incomplete")
	}
	store, _, _ := fixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	schema := "snapshot_" + strings.ReplaceAll(fixtureID(t), "-", "")
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, err := store.pool.Exec(ctx, "CREATE SCHEMA "+quoted); err != nil {
		t.Fatal("isolated schema unavailable")
	}
	t.Cleanup(func() { _, _ = store.pool.Exec(context.Background(), "DROP SCHEMA "+quoted+" CASCADE") })
	if _, err := store.pool.Exec(ctx, "SET search_path TO "+quoted+",public"); err != nil {
		t.Fatal("isolated schema not selected")
	}
	if _, err := store.pool.Exec(ctx, `CREATE TABLE location(id bigint PRIMARY KEY,parent_id bigint,type text,population bigint,languages text[]);
CREATE TABLE location_name(location_id bigint,locale text,name text,is_display boolean);
CREATE TABLE technology(id bigint,slug text);
CREATE TABLE occupation(id bigint,slug text);
CREATE TABLE seniority(id bigint,slug text);
CREATE TABLE currency_rate(currency text,to_eur numeric);`); err != nil {
		t.Fatal("isolated snapshot tables unavailable")
	}
	copyRows := func(table string, columns []string, rows [][]any) {
		t.Helper()
		copied, err := store.pool.CopyFrom(ctx, pgx.Identifier{schema, table}, columns, pgx.CopyFromRows(rows))
		if err != nil || copied != int64(len(rows)) {
			t.Fatal("isolated snapshot import incomplete")
		}
	}
	rows := make([][]any, 0, len(snapshot.Locations))
	for _, row := range snapshot.Locations {
		rows = append(rows, []any{row.ID, row.Parent, row.Type, row.Population, row.Languages})
	}
	copyRows("location", []string{"id", "parent_id", "type", "population", "languages"}, rows)
	rows = make([][]any, 0, len(snapshot.Names))
	for _, row := range snapshot.Names {
		rows = append(rows, []any{row.ID, row.Locale, row.Name, row.Display})
	}
	copyRows("location_name", []string{"location_id", "locale", "name", "is_display"}, rows)
	for _, table := range []struct {
		name   string
		values []lookup
	}{{"technology", snapshot.Technologies}, {"occupation", snapshot.Occupations}, {"seniority", snapshot.Seniorities}} {
		rows = make([][]any, 0, len(table.values))
		for _, row := range table.values {
			rows = append(rows, []any{row.ID, row.Slug})
		}
		copyRows(table.name, []string{"id", "slug"}, rows)
	}
	rows = make([][]any, 0, len(snapshot.Rates))
	for _, row := range snapshot.Rates {
		rows = append(rows, []any{row.Currency, row.ToEUR})
	}
	copyRows("currency_rate", []string{"currency", "to_eur"}, rows)
	started := time.Now()
	lookups, err := LoadLookups(ctx, store)
	if err != nil || len(lookups.technologies) != len(snapshot.Technologies) || len(lookups.occupations) != len(snapshot.Occupations) || len(lookups.seniorities) != len(snapshot.Seniorities) || len(lookups.rates) != len(snapshot.Rates) {
		t.Fatal("native lookup snapshot incomplete")
	}
	loaded, err := LoadLocations(ctx, store)
	if err != nil {
		t.Fatal("native startup location snapshot failed")
	}
	t.Cleanup(func() { _ = loaded.Close() })
	elapsed := time.Since(started)
	var entries, names, display int
	for query, target := range map[string]*int{"SELECT count(*) FROM entry": &entries, "SELECT count(*) FROM name_index": &names, "SELECT count(*) FROM display_name": &display} {
		if err := loaded.index.QueryRowContext(ctx, query).Scan(target); err != nil {
			t.Fatal("private index census failed")
		}
	}
	if entries != len(snapshot.Locations) || names < 1 || display < 1 || store.pool.Stat().TotalConns() != 1 {
		t.Fatal("native startup snapshot or connection budget differs")
	}
	index, err := os.Stat(filepath.Join(loaded.directory, "locations.sqlite3"))
	if err != nil || index.Mode().Perm() != 0o600 || index.Size() >= 32*1024*1024 {
		t.Fatal("native index exceeds private tmpfs envelope")
	}
	ids, kinds, err := loaded.Resolve(ctx, []string{"Zurich, Switzerland"}, "onsite", "en")
	if err != nil || len(ids) != 1 || len(kinds) != 1 || kinds[0] != "onsite" {
		t.Fatal("production startup names did not resolve the fixed geographic case")
	}
	t.Logf("startup_snapshot locations=%d names=%d indexed_names=%d display_names=%d index_bytes=%d load_ms=%d pool_connections=1", entries, len(snapshot.Names), names, display, index.Size(), elapsed.Milliseconds())
	directory := loaded.directory
	if err := loaded.Close(); err != nil {
		t.Fatal("private index cleanup failed")
	}
	if _, err := os.Stat(directory); !os.IsNotExist(err) {
		t.Fatal("private index retained after cleanup")
	}
}
