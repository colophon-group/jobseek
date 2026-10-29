package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"github.com/jackc/pgx/v5"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestLocalMigrationAssetsBindCompleteOriginalSourceChain(t *testing.T) {
	manifest, e := loadLocalMigrations()
	if e != nil || manifest.Head != "0041" || len(manifest.Revisions) != 41 {
		t.Fatal("complete migration chain", e)
	}
	for path, expected := range manifest.SourceHashes {
		if !strings.HasPrefix(path, "src/migrations/") || strings.Contains(path, "..") {
			t.Fatal("source path outside migration tree")
		}
		body, e := os.ReadFile(filepath.Join("../..", path))
		sum := sha256.Sum256(body)
		if e != nil || hex.EncodeToString(sum[:]) != expected {
			t.Fatal("original migration changed; regenerate native assets", path)
		}
	}
}

func migrationFixtureConnection(t *testing.T, ctx context.Context, name string) (*pgx.Conn, string) {
	t.Helper()
	dsn := os.Getenv("JOBSEEK_ORDINARY_QUEUE_TEST_DATABASE_URL")
	if dsn == "" {
		if os.Getenv("JOBSEEK_ORDINARY_QUEUE_REQUIRE_POSTGRES") == "1" {
			t.Fatal("private fixture required")
		}
		t.Skip("requires isolated migrated fixture")
	}
	u, e := url.Parse(dsn)
	if e != nil || u.Hostname() != "127.0.0.1" && u.Hostname() != "localhost" || !strings.HasSuffix(u.Path, "_ordinary_worker_test") {
		t.Fatal("requires private local fixture")
	}
	if name != "jobseek_native1040_migration_go_test" && name != "jobseek_native1040_migration_python_test" {
		t.Fatal("unowned schema fixture name")
	}
	admin, e := pgx.Connect(ctx, dsn)
	if e != nil {
		t.Fatal("fixture administrator unavailable")
	}
	if _, e := admin.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{name}.Sanitize()); e != nil {
		admin.Close(ctx)
		t.Fatal("private migration database already exists or cannot be created")
	}
	t.Cleanup(func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_, e := admin.Exec(cleanup, "DROP DATABASE "+pgx.Identifier{name}.Sanitize()+" WITH (FORCE)")
		if e != nil {
			t.Error("private schema fixture cleanup failed")
		}
		admin.Close(cleanup)
	})
	u.Path = "/" + name
	conn, e := pgx.Connect(ctx, u.String())
	if e != nil {
		t.Fatal("schema fixture unavailable")
	}
	t.Cleanup(func() { conn.Close(context.Background()) })
	return conn, u.String()
}

func schemaMigrationCatalog(ctx context.Context, conn *pgx.Conn) ([]string, error) {
	query := `SELECT entry FROM (
 SELECT 'column:'||c.relname||':'||a.attname||':'||pg_catalog.format_type(a.atttypid,a.atttypmod)||':'||a.attnotnull||':'||COALESCE(pg_get_expr(d.adbin,d.adrelid),'') AS entry FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='public' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
 UNION ALL SELECT 'index:'||indexname||':'||indexdef FROM pg_indexes WHERE schemaname='public'
 UNION ALL SELECT 'constraint:'||c.relname||':'||s.conname||':'||pg_get_constraintdef(s.oid) FROM pg_constraint s JOIN pg_class c ON c.oid=s.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
 UNION ALL SELECT 'function:'||p.proname||':'||pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f'
 UNION ALL SELECT 'trigger:'||tgname||':'||pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal
 UNION ALL SELECT 'sequence:'||sequencename||':'||start_value||':'||min_value||':'||max_value||':'||increment_by||':'||cycle FROM pg_sequences WHERE schemaname='public'
 ) x ORDER BY entry`
	rows, e := conn.Query(ctx, query)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	entries := []string{}
	for rows.Next() {
		var entry string
		if e := rows.Scan(&entry); e != nil {
			return nil, e
		}
		entries = append(entries, entry)
	}
	return entries, rows.Err()
}

func runOriginalMigrationFixture(t *testing.T, ctx context.Context, dsn, target string) {
	t.Helper()
	python, e := filepath.Abs(filepath.Join("../..", ".venv/bin/python"))
	if e != nil {
		t.Fatal(e)
	}
	command := exec.CommandContext(ctx, python, "-m", "alembic", "-c", "src/migrations/alembic.ini", "upgrade", target)
	command.Dir = "../.."
	command.Env = append(os.Environ(), "LOCAL_DATABASE_URL="+dsn, "CRAWLER_DB_ROLE=fixture-migrate")
	if output, e := command.CombinedOutput(); e != nil {
		t.Fatalf("actual original migration failed: %s", output)
	}
}

func TestRealLocalMigrationChunkRepairAndConcurrentIndexMatchAlembic(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	native, _ := migrationFixtureConnection(t, ctx, "jobseek_native1040_migration_go_test")
	original, dsn := migrationFixtureConnection(t, ctx, "jobseek_native1040_migration_python_test")
	if _, e := migrateLocalThrough(ctx, native, "0003"); e != nil {
		t.Fatal(e)
	}
	runOriginalMigrationFixture(t, ctx, dsn, "0003")
	for _, conn := range []*pgx.Conn{native, original} {
		if _, e := conn.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,board_status,is_enabled) VALUES ('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000099','dead','https://dead.invalid','disabled',false),('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000099','paused','https://paused.invalid','active',false); INSERT INTO job_posting(company_id,board_id,source_url,next_scrape_at) SELECT '00000000-0000-0000-0000-000000000099','00000000-0000-0000-0000-000000000001','https://dead.invalid/job/'||n,now() FROM generate_series(1,1001) n;INSERT INTO job_posting(company_id,board_id,source_url,next_scrape_at) VALUES('00000000-0000-0000-0000-000000000099','00000000-0000-0000-0000-000000000002','https://paused.invalid/job/1',now())`); e != nil {
			t.Fatal("source data fixture", e)
		}
	}
	if _, e := migrateLocalThrough(ctx, native, "0004"); e != nil {
		t.Fatal(e)
	}
	runOriginalMigrationFixture(t, ctx, dsn, "0004")
	for _, conn := range []*pgx.Conn{native, original} {
		var dead, paused int
		if e := conn.QueryRow(ctx, `SELECT count(*) FILTER(WHERE board_id='00000000-0000-0000-0000-000000000001' AND NOT is_active AND next_scrape_at IS NULL),count(*) FILTER(WHERE board_id='00000000-0000-0000-0000-000000000002' AND is_active AND next_scrape_at IS NOT NULL) FROM job_posting`).Scan(&dead, &paused); e != nil || dead != 1001 || paused != 1 {
			t.Fatal("original chunked data predicate/conservation", e, dead, paused)
		}
	}
	if _, e := migrateLocalThrough(ctx, native, "0026"); e != nil {
		t.Fatal(e)
	}
	runOriginalMigrationFixture(t, ctx, dsn, "0026")
	for _, conn := range []*pgx.Conn{native, original} {
		if _, e := conn.Exec(ctx, "CREATE TABLE location_name(name text);CREATE INDEX idx_location_name_lower_lookup ON location_name(name)"); e != nil {
			t.Fatal(e)
		}
	}
	if _, e := migrateLocalThrough(ctx, native, "0027"); e == nil {
		t.Fatal("invalid concurrent index migration succeeded")
	}
	python, e := filepath.Abs(filepath.Join("../..", ".venv/bin/python"))
	if e != nil {
		t.Fatal(e)
	}
	command := exec.CommandContext(ctx, python, "-m", "alembic", "-c", "src/migrations/alembic.ini", "upgrade", "0027")
	command.Dir = "../.."
	command.Env = append(os.Environ(), "LOCAL_DATABASE_URL="+dsn, "CRAWLER_DB_ROLE=fixture-migrate")
	if e := command.Run(); e == nil {
		t.Fatal("original invalid concurrent index unexpectedly succeeded")
	}
	for _, conn := range []*pgx.Conn{native, original} {
		stamp, e := currentLocalMigration(ctx, conn)
		if e != nil || stamp != "0026" {
			t.Fatal("failed autocommit operation advanced version", e, stamp)
		}
		if _, e := conn.Exec(ctx, "ALTER TABLE location_name ADD COLUMN location_id bigint"); e != nil {
			t.Fatal(e)
		}
	}
	if _, e := migrateLocalThrough(ctx, native, "0027"); e != nil {
		t.Fatal(e)
	}
	runOriginalMigrationFixture(t, ctx, dsn, "0027")
	a, e := schemaMigrationCatalog(ctx, native)
	if e != nil {
		t.Fatal(e)
	}
	b, e := schemaMigrationCatalog(ctx, original)
	if e != nil || !reflect.DeepEqual(a, b) {
		t.Fatal("conditional concurrent index schema differs", e)
	}
	for _, conn := range []*pgx.Conn{native, original} {
		var valid bool
		var definition string
		if e := conn.QueryRow(ctx, "SELECT indisvalid,pg_get_indexdef(indexrelid) FROM pg_index WHERE indexrelid='idx_location_name_lower_lookup'::regclass").Scan(&valid, &definition); e != nil || !valid || !strings.Contains(definition, "lower(name)") || !strings.Contains(definition, "INCLUDE (location_id)") {
			t.Fatal("original concurrent replacement index", e)
		}
	}
}

func TestRealLocalMigrationFreshSchemaMatchesActualAlembic(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	native, nativeDSN := migrationFixtureConnection(t, ctx, "jobseek_native1040_migration_go_test")
	original, dsn := migrationFixtureConnection(t, ctx, "jobseek_native1040_migration_python_test")
	binary := filepath.Join(t.TempDir(), "go-typesense-exporter")
	if output, e := exec.CommandContext(ctx, "go", "build", "-o", binary, ".").CombinedOutput(); e != nil {
		t.Fatalf("native migration CLI build failed: %s", output)
	}
	nativeCommand := exec.CommandContext(ctx, binary, "--migrate-local")
	nativeCommand.Env = []string{"LOCAL_DATABASE_URL=" + nativeDSN, "CRAWLER_DB_ROLE=fixture-migrate"}
	output, e := nativeCommand.CombinedOutput()
	var result map[string]string
	if e != nil || json.Unmarshal(output, &result) != nil || result["schema_revision"] != "0041" {
		t.Fatalf("compiled native fresh migration failed: %s", output)
	}
	head := result["schema_revision"]
	python := filepath.Join("../..", ".venv/bin/python")
	absolute, e := filepath.Abs(python)
	if e != nil {
		t.Fatal(e)
	}
	command := exec.CommandContext(ctx, absolute, "-m", "alembic", "-c", "src/migrations/alembic.ini", "upgrade", "head")
	command.Dir = "../.."
	command.Env = append(os.Environ(), "LOCAL_DATABASE_URL="+dsn, "CRAWLER_DB_ROLE=fixture-migrate")
	if output, e := command.CombinedOutput(); e != nil {
		t.Fatalf("actual original Alembic fixture failed: %s", output)
	}
	a, e := schemaMigrationCatalog(ctx, native)
	if e != nil {
		t.Fatal(e)
	}
	b, e := schemaMigrationCatalog(ctx, original)
	if e != nil {
		t.Fatal(e)
	}
	if len(a) < 50 || !reflect.DeepEqual(a, b) {
		t.Fatal("complete native/original schema catalog differs", len(a), len(b))
	}
	if repeat, e := migrateLocal(ctx, native); e != nil || repeat != head {
		t.Fatal("native migration at current head not idempotent", e)
	}
	var rows int
	if e := native.QueryRow(ctx, "SELECT count(*) FROM alembic_version WHERE version_num=$1", head).Scan(&rows); e != nil || rows != 1 {
		t.Fatal("original revision stamp semantics")
	}
	proof, _ := json.Marshal(map[string]any{"schema_entries": len(a), "all_original_schema_entries_equal": true, "head": head})
	t.Log(string(proof))
}

func TestRealLocalMigrationRejectsUnknownHeadsAndRollsBackTransactionalDDL(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	conn, _ := migrationFixtureConnection(t, ctx, "jobseek_native1040_migration_go_test")
	if _, e := conn.Exec(ctx, "CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY);INSERT INTO alembic_version VALUES('future')"); e != nil {
		t.Fatal(e)
	}
	if _, e := migrateLocal(ctx, conn); e == nil {
		t.Fatal("unknown revision accepted")
	}
	if _, e := conn.Exec(ctx, "DELETE FROM alembic_version;INSERT INTO alembic_version VALUES('0001'),('0002')"); e != nil {
		t.Fatal(e)
	}
	if _, e := migrateLocal(ctx, conn); e == nil {
		t.Fatal("multiple heads accepted")
	}
	if _, e := conn.Exec(ctx, "DELETE FROM alembic_version"); e != nil {
		t.Fatal(e)
	}
	if _, e := migrateLocalThrough(ctx, conn, "0001"); e != nil {
		t.Fatal("existing empty version table", e)
	}
	if _, e := conn.Exec(ctx, "ALTER TABLE job_posting DROP COLUMN location_types"); e != nil {
		t.Fatal(e)
	}
	if _, e := migrateLocalThrough(ctx, conn, "0002"); e == nil {
		t.Fatal("broken transactional DDL accepted")
	}
	stamp, e := currentLocalMigration(ctx, conn)
	var created *string
	if e != nil || stamp != "0001" || conn.QueryRow(ctx, "SELECT to_regclass('public.company')::text").Scan(&created) != nil || created != nil {
		t.Fatal("failed transactional revision retained DDL or advanced stamp", e, stamp)
	}
	if _, e := conn.Exec(ctx, "ALTER TABLE job_posting ADD COLUMN location_types text[]"); e != nil {
		t.Fatal(e)
	}
	if head, e := migrateLocalThrough(ctx, conn, "0002"); e != nil || head != "0002" {
		t.Fatal("transactional retry failed", e)
	}
}
