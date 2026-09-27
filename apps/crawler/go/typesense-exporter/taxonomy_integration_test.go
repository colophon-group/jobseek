package main

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

type taxonomySnapshotTracer struct {
	afterFirstRead func()
	begin          string
}

func (trace *taxonomySnapshotTracer) TraceQueryStart(ctx context.Context, _ *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	if strings.HasPrefix(data.SQL, "begin ") {
		trace.begin = data.SQL
	}
	return context.WithValue(ctx, taxonomyQueryKey{}, data.SQL)
}

type taxonomyQueryKey struct{}

func (trace *taxonomySnapshotTracer) TraceQueryEnd(ctx context.Context, _ *pgx.Conn, _ pgx.TraceQueryEndData) {
	query, _ := ctx.Value(taxonomyQueryKey{}).(string)
	if strings.HasPrefix(query, "SELECT row_to_json(t)") && trace.afterFirstRead != nil {
		callback := trace.afterFirstRead
		trace.afterFirstRead = nil
		callback()
	}
}

func TestTaxonomyPostgresUsesOneReadOnlySnapshot(t *testing.T) {
	dsn := os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("GO_TYPESENSE_TEST_DATABASE_URL is not set")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	observer, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer observer.Close(context.Background())
	schema := pgx.Identifier{fmt.Sprintf("taxonomy_test_%d", time.Now().UnixNano())}.Sanitize()
	if _, err := observer.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		t.Fatal(err)
	}
	defer observer.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE")
	if _, err := observer.Exec(ctx, "SET search_path TO "+schema); err != nil {
		t.Fatal(err)
	}
	_, err = observer.Exec(ctx, `
CREATE TABLE location (id int,type text,lat double precision,lng double precision,slug text,population bigint,parent_id int);
CREATE TABLE location_name (location_id int,locale text,name text,is_display bool);
CREATE TABLE location_macro_member (macro_id int,country_id int);
CREATE TABLE occupation (id int,slug text,parent_id int,domain_id int);
CREATE TABLE occupation_name (occupation_id int,locale text,name text,is_display bool);
CREATE TABLE occupation_domain (id int,slug text);
CREATE TABLE occupation_domain_name (domain_id int,locale text,name text,is_display bool);
CREATE TABLE seniority (id int,slug text);
CREATE TABLE seniority_name (seniority_id int,locale text,name text,is_display bool);
CREATE TABLE technology (id int,slug text,name text,category text);
CREATE TABLE company (id uuid,industry int);
CREATE TABLE industry (id int,name text);
CREATE TABLE industry_name (industry_id int,locale text,name text,is_display bool);
INSERT INTO location VALUES(1,'country',NULL,NULL,'country',123,NULL),(2,'city',1.0,-0.0,'city',456,1);
INSERT INTO location_name VALUES(1,'en','Country',true),(2,'en','City',true),(2,'fr','Ville',true);
INSERT INTO occupation VALUES(1,'engineer',NULL,1);
INSERT INTO occupation_name VALUES(1,'en','Engineer',true),(1,'*','Dev',false);
INSERT INTO occupation_domain VALUES(1,'engineering');
INSERT INTO occupation_domain_name VALUES(1,'en','Engineering',true);
INSERT INTO seniority VALUES(1,'junior');
INSERT INTO seniority_name VALUES(1,'en','Junior',true);
INSERT INTO technology VALUES(1,'go','Go','language');
INSERT INTO company VALUES('00000000-0000-0000-0000-000000000001',1);
INSERT INTO industry VALUES(1,'Before concurrent sync');
INSERT INTO industry_name VALUES(1,'de','Vorher',true);
`)
	if err != nil {
		t.Fatal(err)
	}
	changed := false
	trace := &taxonomySnapshotTracer{afterFirstRead: func() {
		_, err := observer.Exec(ctx, "UPDATE industry SET name='After concurrent sync'")
		if err != nil {
			t.Error(err)
		} else {
			changed = true
		}
	}}
	config, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	config.Tracer = trace
	conn, err := pgx.ConnectConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(context.Background())
	if _, err := conn.Exec(ctx, "SET search_path TO "+schema); err != nil {
		t.Fatal(err)
	}
	contract, err := loadTaxonomyContract()
	if err != nil {
		t.Fatal(err)
	}
	documents, err := loadTaxonomySnapshot(ctx, conn, contract)
	if err != nil {
		t.Fatal(err)
	}
	if !changed || !strings.Contains(trace.begin, "repeatable read") || !strings.Contains(trace.begin, "read only") {
		t.Fatalf("snapshot not proven: changed=%v begin=%s", changed, trace.begin)
	}
	if documents["company"][0]["industry_name"] != "Before concurrent sync" {
		t.Fatal("authority spanned multiple snapshots")
	}
	if len(documents["location"]) != 2 || documents["location"][1]["parent_name"] != "Country" || documents["occupation"][0]["domain_name"] != "Engineering" {
		t.Fatal("SQL projection omitted joined authority")
	}
	var live string
	if err := observer.QueryRow(ctx, "SELECT name FROM industry").Scan(&live); err != nil || live != "After concurrent sync" {
		t.Fatal("concurrent update did not commit")
	}
	if conn.PgConn().TxStatus() != 'I' {
		t.Fatal("snapshot transaction was not released")
	}
	if _, err := observer.Exec(ctx, `
ALTER TABLE company ADD COLUMN name text DEFAULT 'Fixture Company', ADD COLUMN slug text DEFAULT 'fixture-company',
 ADD COLUMN icon text, ADD COLUMN logo text, ADD COLUMN website text,
 ADD COLUMN employee_count_range int, ADD COLUMN founded_year int;
CREATE TABLE company_description (company_id uuid,locale text,description text);
INSERT INTO company_description VALUES('00000000-0000-0000-0000-000000000001','fr','<p>Bonjour</p>');
`); err != nil {
		t.Fatal(err)
	}
	fullDocs, err := loadSyncTaxonomySnapshot(ctx, conn, contract)
	if err != nil {
		t.Fatal(err)
	}
	if fullDocs["company"][0]["description_fr"] != "<p>Bonjour</p>" || fullDocs["company"][0]["name"] != "Fixture Company" {
		t.Fatal("company detail authority omitted fields")
	}
	testSyncRuntimeFixture(t, dsn, strings.Trim(schema, "\""))
	// Authority failures must also roll the snapshot back.
	if _, err := observer.Exec(ctx, "UPDATE location SET parent_id=2 WHERE id=1"); err != nil {
		t.Fatal(err)
	}
	if _, err := loadTaxonomySnapshot(ctx, conn, contract); err == nil {
		t.Fatal("hierarchy cycle accepted")
	}
	if conn.PgConn().TxStatus() != 'I' {
		t.Fatal("failed snapshot leaked transaction")
	}
}
