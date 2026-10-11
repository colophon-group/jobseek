package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func TestLocationRepairPostgresFillsMissingFieldsAndRollsBackDrift(t *testing.T) {
	for _, mode := range []string{"success", "populated-slug-conflict", "populated-coordinate-conflict", "source-blank", "source-duplicate-slug", "source-partial-coordinates", "id-drift", "missing-constraint", "postflight-drift", "canceled"} {
		t.Run(mode, func(t *testing.T) {
			ctx, local, observer := registryTestDatabase(t)
			source, err := pgx.Connect(ctx, os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL"))
			if err != nil {
				t.Fatal(err)
			}
			defer source.Close(context.Background())
			schema := pgx.Identifier{fmt.Sprintf("repair_source_%d", time.Now().UnixNano())}.Sanitize()
			if _, err = source.Exec(ctx, "CREATE SCHEMA "+schema+";SET search_path TO "+schema); err != nil {
				t.Fatal(err)
			}
			defer source.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE")
			if _, err = source.Exec(ctx, `CREATE TABLE location(id INTEGER PRIMARY KEY,slug TEXT,lat REAL,lng REAL);INSERT INTO location VALUES(1,'alpha',1.25,2.5),(2,'beta',NULL,NULL)`); err != nil {
				t.Fatal(err)
			}
			if _, err = local.Exec(ctx, `CREATE TABLE location(id INTEGER PRIMARY KEY,slug TEXT,lat REAL,lng REAL);INSERT INTO location VALUES(1,NULL,NULL,2.5),(2,'beta',NULL,NULL);ALTER TABLE location ADD CONSTRAINT chk_location_slug_nonblank CHECK(slug IS NOT NULL AND btrim(slug)<>'') NOT VALID`); err != nil {
				t.Fatal(err)
			}
			switch mode {
			case "populated-slug-conflict":
				_, err = local.Exec(ctx, `UPDATE location SET slug='wrong' WHERE id=2`)
			case "populated-coordinate-conflict":
				_, err = local.Exec(ctx, `UPDATE location SET lat=9 WHERE id=2`)
			case "source-blank":
				_, err = source.Exec(ctx, `UPDATE location SET slug=' ' WHERE id=1`)
			case "source-duplicate-slug":
				_, err = source.Exec(ctx, `UPDATE location SET slug='alpha' WHERE id=2`)
			case "source-partial-coordinates":
				_, err = source.Exec(ctx, `UPDATE location SET lng=NULL WHERE id=1`)
			case "id-drift":
				_, err = source.Exec(ctx, `UPDATE location SET id=3 WHERE id=2`)
			case "missing-constraint":
				_, err = local.Exec(ctx, `ALTER TABLE location DROP CONSTRAINT chk_location_slug_nonblank`)
			case "postflight-drift":
				_, err = local.Exec(ctx, `CREATE FUNCTION corrupt_repair() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.lat=42; RETURN NEW; END $$;CREATE TRIGGER corrupt BEFORE UPDATE ON location FOR EACH ROW EXECUTE FUNCTION corrupt_repair()`)
			}
			if err != nil {
				t.Fatal(err)
			}
			var before string
			if err = observer.QueryRow(ctx, `SELECT json_agg(x ORDER BY id)::text FROM location x`).Scan(&before); err != nil {
				t.Fatal(err)
			}
			operationCtx := ctx
			if mode == "canceled" {
				var cancel context.CancelFunc
				operationCtx, cancel = context.WithCancel(ctx)
				cancel()
			}
			summary, err := repairLocationTaxonomy(operationCtx, source, local, 2)
			if mode != "success" {
				if err == nil {
					t.Fatal("unproved repair committed", summary)
				}
				var after string
				if e := observer.QueryRow(ctx, `SELECT json_agg(x ORDER BY id)::text FROM location x`).Scan(&after); e != nil || before != after {
					t.Fatal("failed repair changed target", e)
				}
				return
			}
			want := locationRepairSummary{2, 2, 2, 1, 1, 3, 1, true, true}
			if err != nil || summary != want {
				t.Fatal("repair summary differs", summary, err)
			}
			sourceTx, e := source.Begin(ctx)
			if e != nil {
				t.Fatal(e)
			}
			s, e := readRepairLocations(ctx, sourceTx)
			sourceTx.Rollback(ctx)
			if e != nil {
				t.Fatal(e)
			}
			localTx, e := local.Begin(ctx)
			if e != nil {
				t.Fatal(e)
			}
			l, e := readRepairLocations(ctx, localTx)
			localTx.Rollback(ctx)
			if e != nil || !reflect.DeepEqual(s, l) {
				t.Fatal("canonical postflight differs", e)
			}
			second, e := repairLocationTaxonomy(ctx, source, local, 2)
			if e != nil || second.UpdatedRows != 0 || !second.SourceLocalEqual || !second.ConstraintValidated {
				t.Fatal("idempotent repair changed", second, e)
			}
		})
	}
}

func TestLocationRepairSourceCardinalityIsFixed(t *testing.T) {
	if expectedLocationRows != 37526 || validateRepairSource(nil, expectedLocationRows) == nil {
		t.Fatal("fixed canonical cardinality lost")
	}
}

func TestLocationRepairPostgresComplete37526RowSnapshot(t *testing.T) {
	ctx, local, observer := registryTestDatabase(t)
	source, err := pgx.Connect(ctx, os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close(context.Background())
	schema := pgx.Identifier{fmt.Sprintf("repair_full_%d", time.Now().UnixNano())}.Sanitize()
	if _, err = source.Exec(ctx, "CREATE SCHEMA "+schema+";SET search_path TO "+schema); err != nil {
		t.Fatal(err)
	}
	defer source.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE")
	if _, err = source.Exec(ctx, `CREATE TABLE location(id INTEGER PRIMARY KEY,slug TEXT,lat REAL,lng REAL);
	 INSERT INTO location SELECT n,'location-'||n,1.25::real,2.5::real FROM generate_series(1,37526) n`); err != nil {
		t.Fatal(err)
	}
	if _, err = local.Exec(ctx, `CREATE TABLE location(id INTEGER PRIMARY KEY,slug TEXT,lat REAL,lng REAL);
	 INSERT INTO location SELECT n,NULL,NULL,NULL FROM generate_series(1,37526) n;
	 ALTER TABLE location ADD CONSTRAINT chk_location_slug_nonblank CHECK(slug IS NOT NULL AND btrim(slug)<>'') NOT VALID`); err != nil {
		t.Fatal(err)
	}
	summary, err := repairLocationTaxonomy(ctx, source, local, expectedLocationRows)
	if err != nil || summary.UpdatedRows != 37526 || summary.MissingCoordinateValuesBefore != 75052 || !summary.SourceLocalEqual || !summary.ConstraintValidated {
		t.Fatal(summary, err)
	}
	var count int
	if err = observer.QueryRow(ctx, `SELECT count(*) FROM location WHERE slug='location-'||id AND lat=1.25 AND lng=2.5`).Scan(&count); err != nil || count != 37526 {
		t.Fatal("complete target snapshot differs", count, err)
	}
	var localSchema, sourceSchema string
	if local.QueryRow(ctx, `SELECT current_schema()`).Scan(&localSchema) != nil || source.QueryRow(ctx, `SELECT current_schema()`).Scan(&sourceSchema) != nil {
		t.Fatal("isolated schema identity missing")
	}
	dsn := func(schema string) string {
		u, err := url.Parse(os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL"))
		if err != nil {
			t.Fatal(err)
		}
		q := u.Query()
		q.Set("search_path", schema)
		u.RawQuery = q.Encode()
		return u.String()
	}
	binary := filepath.Join(t.TempDir(), "go-typesense-exporter")
	if out, err := exec.CommandContext(ctx, "go", "build", "-o", binary, ".").CombinedOutput(); err != nil {
		t.Fatalf("native maintenance build: %v %s", err, out)
	}
	command := exec.CommandContext(ctx, binary, "--repair-location-taxonomy-source")
	command.Env = append(os.Environ(), "LOCAL_DATABASE_URL="+dsn(localSchema), "WEB_DATABASE_URL="+dsn(sourceSchema), "CRAWLER_DB_ROLE=location-taxonomy-repair")
	out, err := command.CombinedOutput()
	want := locationRepairSummary{37526, 37526, 37526, 37526, 0, 0, 0, true, true}
	var result locationRepairSummary
	if err != nil || json.Unmarshal(out, &result) != nil || result != want || len(out) > 4096 {
		t.Fatal("compiled command did not preserve bounded full-snapshot evidence", err)
	}
	for _, required := range []string{`"expected_rows": 37526`, `"source_rows": 37526`, `"local_rows": 37526`, `"source_local_equal": true`, `"constraint_validated": true`} {
		if !strings.Contains(string(out), required) {
			t.Fatal("protected workflow evidence changed", required)
		}
	}
	if strings.Contains(string(out), "postgres://") || strings.Contains(string(out), "postgresql://") {
		t.Fatal("compiled evidence disclosed database configuration")
	}
	command = exec.CommandContext(ctx, binary, "--repair-location-taxonomy-source", "--expected-rows", "2")
	command.Env = []string{}
	if command.Run() == nil || command.ProcessState.ExitCode() != 2 {
		t.Fatal("compiled command allowed configurable canonical cardinality")
	}
	command = exec.CommandContext(ctx, binary, "--refresh-currency-rates", "--endpoint", "http://127.0.0.1:1")
	command.Env = []string{}
	if command.Run() == nil || command.ProcessState.ExitCode() != 2 {
		t.Fatal("compiled currency command allowed endpoint override")
	}
}
