package executor

import (
	"context"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func TestPostgresNativeLookupSnapshotAndSingleConnection(t *testing.T) {
	store, _, _ := fixture(t)
	ctx := context.Background()
	schema := "lookups_" + strings.ReplaceAll(fixtureID(t), "-", "")
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, err := store.pool.Exec(ctx, "CREATE SCHEMA "+quoted); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = store.pool.Exec(context.Background(), "DROP SCHEMA "+quoted+" CASCADE") })
	if _, err := store.pool.Exec(ctx, "SET search_path TO "+quoted+",public"); err != nil {
		t.Fatal(err)
	}
	if _, err := store.pool.Exec(ctx, `
CREATE TABLE technology(id bigint,slug text);
CREATE TABLE occupation(id bigint,slug text);
CREATE TABLE seniority(id bigint,slug text);
CREATE TABLE currency_rate(currency text,to_eur numeric);
INSERT INTO technology VALUES(5,'python'),(7,'go');
INSERT INTO occupation VALUES(3,'software-engineer');
INSERT INTO seniority VALUES(9,'intern');
INSERT INTO currency_rate VALUES('CHF',1.05),('USD',0.9);
`); err != nil {
		t.Fatal(err)
	}
	deadline, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	l, err := LoadLookups(deadline, store)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(l.technologies, map[string]int64{"python": 5, "go": 7}) || l.occupations["software-engineer"] != 3 || l.seniorities["intern"] != 9 || l.rates["CHF"] != 1.05 || l.rates["USD"] != 0.9 {
		t.Fatalf("lookup snapshot differs: %+v", l)
	}
	if store.pool.Stat().TotalConns() != 1 {
		t.Fatal("lookup escaped one-connection budget")
	}
	if _, err := store.pool.Exec(ctx, "UPDATE currency_rate SET to_eur=2"); err != nil {
		t.Fatal(err)
	}
	if l.rates["CHF"] != 1.05 {
		t.Fatal("singleton snapshot changed after load")
	}
	canceled, stop := context.WithCancel(ctx)
	stop()
	if _, err := LoadLookups(canceled, store); err == nil {
		t.Fatal("canceled loader accepted work")
	}
}
