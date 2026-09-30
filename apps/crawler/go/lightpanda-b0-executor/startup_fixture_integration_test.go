package executor

import (
	"context"
	"net/url"
	"os"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
)

// Exercise the same reference-table fixture used by the installed image volume
// contract, in an owned private schema. It must support real native startup,
// rather than making that CI check select the old Python owner again.
func TestPostgresNativeImageStartupFixture(t *testing.T) {
	store, _, _ := fixture(t)
	ctx := context.Background()
	schema := "startup_" + strings.ReplaceAll(fixtureID(t), "-", "")
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, err := store.pool.Exec(ctx, "CREATE SCHEMA "+quoted); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = store.pool.Exec(context.Background(), "DROP SCHEMA "+quoted+" CASCADE") })
	sql, err := os.ReadFile("testdata/startup-fixture.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.pool.Exec(ctx, strings.ReplaceAll(string(sql), "public.", quoted+".")); err != nil {
		t.Fatal(err)
	}
	parsed, err := url.Parse(os.Getenv("JOBSEEK_B0_EXECUTOR_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	query := parsed.Query()
	query.Set("search_path", schema+",public")
	parsed.RawQuery = query.Encode()
	native, err := OpenStore(ctx, parsed.String())
	if err != nil {
		t.Fatal(err)
	}
	defer native.Close()
	lookups, err := LoadLookups(ctx, native)
	if err != nil {
		t.Fatal("installed image fixture cannot load native reference tables")
	}
	if lookups.technologies["python"] != 4 || lookups.occupations["software-engineer"] != 41 || lookups.seniorities["intern"] != 9 || lookups.rates["CHF"] != 1.05 {
		t.Fatal("installed image startup fixture values differ")
	}
	locations, err := LoadLocations(ctx, native)
	if err != nil {
		t.Fatal("installed image fixture cannot load native location index")
	}
	defer locations.Close()
	if native.pool.Config().MaxConns != 1 || native.pool.Config().MinConns != 1 {
		t.Fatal("fixture changed native connection budget")
	}
}
