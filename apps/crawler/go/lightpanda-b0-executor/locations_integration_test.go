package executor

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func TestPostgresNativeLocationsLoadBackfillAndPrivateLifecycle(t *testing.T) {
	store, _, _ := fixture(t)
	ctx := context.Background()
	schema := "locations_" + strings.ReplaceAll(fixtureID(t), "-", "")
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, err := store.pool.Exec(ctx, "CREATE SCHEMA "+quoted); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = store.pool.Exec(context.Background(), "DROP SCHEMA "+quoted+" CASCADE") })
	if _, err := store.pool.Exec(ctx, "SET search_path TO "+quoted+",public"); err != nil {
		t.Fatal(err)
	}
	if _, err := store.pool.Exec(ctx, `
CREATE TABLE location(id bigint PRIMARY KEY,parent_id bigint,type text,population bigint,languages text[]);
CREATE TABLE location_name(location_id bigint,locale text,name text,is_display boolean);
INSERT INTO location VALUES(1,NULL,'country',NULL,ARRAY['de']),(2,1,'city',400000,ARRAY['de']),(3,4,'city',14000000,ARRAY['ja']),(4,NULL,'country',125000000,ARRAY['ja']);
INSERT INTO location_name VALUES(1,'en','Switzerland',true),(1,'de','Schweiz',false),(2,'en','Zürich',false),(2,'en','Zurich',true),(3,'en','Tokyo',false),(3,'ja','東京',true),(4,'en','Japan',true);
`); err != nil {
		t.Fatal(err)
	}
	loaded, err := LoadLocations(ctx, store)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = loaded.Close() })
	directory := loaded.directory
	for path, mode := range map[string]os.FileMode{directory: 0o700, filepath.Join(directory, "locations.sqlite3"): 0o600} {
		info, err := os.Stat(path)
		if err != nil || info.Mode().Perm() != mode {
			t.Fatalf("private index metadata mismatch: %s %v", path, err)
		}
	}
	ids, kinds, err := loaded.Resolve(ctx, []string{"Zürich, Switzerland"}, "onsite", "de")
	if err != nil || !reflect.DeepEqual(ids, []int64{2}) || !reflect.DeepEqual(kinds, []string{"onsite"}) {
		t.Fatalf("core load mismatch: %v %v %v", ids, kinds, err)
	}
	display, err := loaded.resolver.DisplayName(2)
	if err != nil || display == nil || *display != "Zurich" {
		t.Fatalf("display override mismatch: %v %v", display, err)
	}
	ids, kinds, err = loaded.Resolve(ctx, []string{"東京"}, "hybrid", "ja")
	if err != nil || !reflect.DeepEqual(ids, []int64{3}) || !reflect.DeepEqual(kinds, []string{"hybrid"}) {
		t.Fatalf("native non-core backfill mismatch: %v %v %v", ids, kinds, err)
	}
	if store.pool.Stat().TotalConns() != 1 {
		t.Fatal("location loader escaped connection budget")
	}
	// A real 549-key read crosses the existing 500-key boundary without a
	// nested acquire. Only the observed names enter the monotone negative cache.
	missed := make([]string, 549)
	beforeNegative := len(loaded.negative)
	for i := range missed {
		missed[i] = fmt.Sprintf("unknownfixture%04d", i)
	}
	deadline, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	if changed, err := loaded.backfill(deadline, missed); err != nil || changed || len(loaded.negative) != beforeNegative+549 {
		t.Fatalf("bounded negative backfill mismatch: %t %d %v", changed, len(loaded.negative), err)
	}
	if err := loaded.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(directory); !os.IsNotExist(err) {
		t.Fatal("owned index not removed")
	}
	if _, _, err := loaded.Resolve(ctx, []string{"Zurich"}, "onsite", "de"); err == nil {
		t.Fatal("closed index accepted work")
	}
}
