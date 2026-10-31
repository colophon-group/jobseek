package enrichment

import (
	"database/sql"
	"fmt"
	"maps"
	"path/filepath"
	"reflect"
	"testing"
)

func locationCacheResolver(tb testing.TB) *LocationResolver {
	tb.Helper()
	path := filepath.Join(tb.TempDir(), "locations.sqlite")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		tb.Fatal(err)
	}
	_, err = db.Exec(`
CREATE TABLE entry(id INTEGER PRIMARY KEY,parent_id INTEGER,loc_type TEXT,population INTEGER,languages TEXT);
CREATE TABLE name_index(name TEXT,location_id INTEGER);
CREATE INDEX idx_name ON name_index(name);
CREATE TABLE display_name(location_id INTEGER PRIMARY KEY,name TEXT);
INSERT INTO entry VALUES(1,NULL,'country',100,'fr'),(2,1,'city',10,'fr');
INSERT INTO name_index VALUES('france',1),('paris',2);
INSERT INTO display_name VALUES(1,'France'),(2,'Paris');`)
	if closeErr := db.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		tb.Fatal(err)
	}
	r, err := OpenLocations(path)
	if err != nil {
		tb.Fatal(err)
	}
	tb.Cleanup(func() { _ = r.Close() })
	return r
}

func TestBorrowedLocationCacheUpdatesAndOwnership(t *testing.T) {
	r := locationCacheResolver(t)
	owned := map[string]bool{"owned-miss": true}
	r.negative = owned
	negative := map[string]bool{}
	_, missed, _, err := r.ResolveWithNegativeCache([]string{"unknownfixturevillage"}, "", "en", true, negative)
	if err != nil || len(missed) == 0 || len(negative) != 0 {
		t.Fatal("borrowed resolver mutated its caller's cache or hid new misses")
	}
	for _, key := range missed {
		negative[key] = true
	}
	before := maps.Clone(negative)
	_, missed, _, err = r.ResolveWithNegativeCache([]string{"unknownfixturevillage"}, "", "en", true, negative)
	if err != nil || len(missed) != 0 || !reflect.DeepEqual(negative, before) || !reflect.DeepEqual(r.negative, owned) {
		t.Fatal("updated miss cache was ignored, changed or retained")
	}
	negative["must-not-be-retained"] = true
	if r.negative["must-not-be-retained"] {
		t.Fatal("borrowed cache escaped the serialized call")
	}
	if err := r.Close(); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err := r.ResolveWithNegativeCache([]string{"Paris"}, "", "en", true, negative); err == nil || !reflect.DeepEqual(r.negative, owned) {
		t.Fatal("resolver failure lost owned state or returned empty success")
	}
}

// Reproduce the current executor's map-to-slice and resolver's slice-to-map
// work around a real indexed lookup. This isolates its cache-size dependence;
// it does not identify the size or bottleneck of a production worker.
func BenchmarkLocationNegativeCacheRebuild(b *testing.B) {
	for _, size := range []int{0, 1000, 10000} {
		b.Run(fmt.Sprint(size), func(b *testing.B) {
			r := locationCacheResolver(b)
			negative := make(map[string]bool, size)
			for i := range size {
				negative[fmt.Sprintf("missing-location-%d", i)] = true
			}
			b.ReportAllocs()
			b.ResetTimer()
			for range b.N {
				keys := make([]string, 0, len(negative))
				for key := range negative {
					keys = append(keys, key)
				}
				results, _, _, err := r.Resolve([]string{"Paris"}, "", "en", true, keys)
				if err != nil || len(results) != 1 || results[0].IDValue() != 2 {
					b.Fatal("indexed fixture lookup differs", err)
				}
			}
		})
	}
}

func BenchmarkLocationBorrowedNegativeCache(b *testing.B) {
	for _, size := range []int{0, 1000, 10000} {
		b.Run(fmt.Sprint(size), func(b *testing.B) {
			r := locationCacheResolver(b)
			negative := make(map[string]bool, size)
			for i := range size {
				negative[fmt.Sprintf("missing-location-%d", i)] = true
			}
			b.ReportAllocs()
			b.ResetTimer()
			for range b.N {
				results, _, _, err := r.ResolveWithNegativeCache([]string{"Paris"}, "", "en", true, negative)
				if err != nil || len(results) != 1 || results[0].IDValue() != 2 {
					b.Fatal("indexed fixture lookup differs", err)
				}
			}
		})
	}
}
