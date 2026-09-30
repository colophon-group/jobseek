package enrichment

import (
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
)

type frozenLocation struct {
	Fixtures map[string]struct {
		Entries [][]json.RawMessage `json:"entries"`
		Names   [][]json.RawMessage `json:"names"`
		Display [][]json.RawMessage `json:"display"`
	} `json:"fixtures"`
	Cases []struct {
		Fixture        string           `json:"fixture"`
		Raw            []string         `json:"raw"`
		Fallback       string           `json:"fallback"`
		Language       string           `json:"language"`
		Tracking       bool             `json:"tracking"`
		Negative       []string         `json:"negative"`
		MissesBefore   []string         `json:"misses_before"`
		LookupMisses   []string         `json:"lookup_misses"`
		LocationMisses []LocationMiss   `json:"location_misses"`
		Expected       []LocationResult `json:"expected"`
	} `json:"cases"`
}

func TestFrozenLocationParity(t *testing.T) {
	data, err := os.ReadFile("testdata/python_location.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus frozenLocation
	if err = json.Unmarshal(data, &corpus); err != nil {
		t.Fatal(err)
	}
	resolvers := map[string]*LocationResolver{}
	for signature, fixture := range corpus.Fixtures {
		path := filepath.Join(t.TempDir(), "locations.sqlite")
		db, err := sql.Open("sqlite", path)
		if err != nil {
			t.Fatal(err)
		}
		_, err = db.Exec(`CREATE TABLE entry(id INTEGER PRIMARY KEY,parent_id INTEGER,loc_type TEXT,population INTEGER,languages TEXT);CREATE TABLE name_index(name TEXT,location_id INTEGER);CREATE INDEX idx_name ON name_index(name);CREATE TABLE display_name(location_id INTEGER PRIMARY KEY,name TEXT);`)
		if err != nil {
			t.Fatal(err)
		}
		tx, err := db.Begin()
		if err != nil {
			t.Fatal(err)
		}
		for _, table := range []struct {
			name         string
			values       [][]json.RawMessage
			placeholders string
		}{{"entry", fixture.Entries, "?,?,?,?,?"}, {"name_index", fixture.Names, "?,?"}, {"display_name", fixture.Display, "?,?"}} {
			for _, row := range table.values {
				args := []any{}
				for _, value := range row {
					var v any
					if err = json.Unmarshal(value, &v); err != nil {
						t.Fatal(err)
					}
					args = append(args, v)
				}
				if _, err = tx.Exec("INSERT INTO "+table.name+" VALUES ("+table.placeholders+")", args...); err != nil {
					t.Fatal(err)
				}
			}
		}
		if err = tx.Commit(); err != nil {
			t.Fatal(err)
		}
		db.Close()
		r, err := OpenLocations(path)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { r.Close() })
		resolvers[signature] = r
	}
	for i, c := range corpus.Cases {
		r := resolvers[c.Fixture]
		actual, keys, misses, err := r.Resolve(c.Raw, c.Fallback, c.Language, c.Tracking, c.Negative)
		if err != nil {
			t.Fatalf("case %d %q: %v", i, c.Raw, err)
		}
		if !reflect.DeepEqual(actual, c.Expected) {
			t.Errorf("case %d %q: got %v want %v", i, c.Raw, locationIDs(actual), locationIDs(c.Expected))
		}
		lookup := map[string]bool{}
		for _, key := range append(keys, c.MissesBefore...) {
			lookup[key] = true
		}
		all := []string{}
		for key := range lookup {
			all = append(all, key)
		}
		sort.Strings(all)
		if !reflect.DeepEqual(all, c.LookupMisses) {
			t.Errorf("case %d lookup misses: got %v want %v", i, all, c.LookupMisses)
		}
		if !reflect.DeepEqual(misses, c.LocationMisses) {
			t.Errorf("case %d location misses: got %v want %v", i, misses, c.LocationMisses)
		}
	}
}
func locationIDs(values []LocationResult) []any {
	out := []any{}
	for _, v := range values {
		out = append(out, []any{v.IDValue(), v.Type})
	}
	return out
}

func TestFrozenLocationContextSetOrder(t *testing.T) {
	data, err := os.ReadFile("testdata/python_location_sets.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Input []int64 `json:"input"`
		Order []int64 `json:"order"`
	}
	if err = json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for i, c := range cases {
		if order := locationSetOrder(c.Input); !reflect.DeepEqual(order, c.Order) {
			t.Fatalf("context tie case %d: got %v want %v", i, order, c.Order)
		}
	}
}
