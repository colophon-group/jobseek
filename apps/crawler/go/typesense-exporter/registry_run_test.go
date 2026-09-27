package main

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

type registryLostCommitAck struct{ pgx.Tx }

func (tx registryLostCommitAck) Commit(ctx context.Context) error {
	if err := tx.Tx.Commit(ctx); err != nil {
		return err
	}
	return errors.New("fixture lost commit acknowledgement")
}

type registryLostAckConnection struct{ Conn *pgx.Conn }

func (c registryLostAckConnection) Begin(ctx context.Context) (pgx.Tx, error) {
	tx, err := c.Conn.Begin(ctx)
	if err != nil {
		return nil, err
	}
	return registryLostCommitAck{tx}, nil
}

func TestRegistryPostgresAmbiguousCommitCannotPublish(t *testing.T) {
	ctx, conn, _ := registryTestDatabase(t)
	table, err := parseRegistryCSV([]byte("slug,name\nambiguous,Committed\n"))
	if err != nil {
		t.Fatal(err)
	}
	plan, err := registryCompanyPlan(table, registryTable{})
	if err != nil {
		t.Fatal(err)
	}
	effects, err := commitRegistry(ctx, registryLostAckConnection{conn}, map[string]registryTable{}, plan, nil)
	if err == nil || len(effects.Schedules) != 0 || len(effects.Orphans) != 0 {
		t.Fatalf("ambiguous commit authorized publication: %+v %v", effects, err)
	}
	var count int
	if err = conn.QueryRow(ctx, "SELECT count(*) FROM company WHERE slug='ambiguous'").Scan(&count); err != nil || count != 1 {
		t.Fatalf("fixture did not commit before acknowledgement loss: %d %v", count, err)
	}
}

func TestRegistryMountProvenance(t *testing.T) {
	for _, c := range []struct {
		Path, Text string
		Want       bool
	}{
		{"/app/data", "1 2 0:1 / /app/data ro,relatime - ext4 /dev/a ro", true},
		{"/app/data", "1 2 0:1 / /app rw - ext4 /dev/a rw", false},
		{"/app/data", "1 2 0:1 / /app/data rw,relatime - ext4 /dev/a ro", false},
		{"/app/data", "1 2 0:1 / /app/data-more ro - ext4 /dev/a ro", false},
		{"/app/with space", "1 2 0:1 / /app/with\\040space ro - ext4 /dev/a ro", true},
		{"/app/data", "malformed", false},
	} {
		if got := registryReadonlyMount(c.Path, c.Text); got != c.Want {
			t.Fatalf("mount %q returned %v", c.Text, got)
		}
	}
	root := t.TempDir()
	data := filepath.Join(root, "data")
	if err := os.Mkdir(data, 0700); err != nil {
		t.Fatal(err)
	}
	if _, err := registryDataDirectory(data); err == nil {
		t.Fatal("arbitrary directory accepted as checkout")
	}
}

func TestRegistryPostgresLocationIndexRepair(t *testing.T) {
	ctx, admin, _ := registryTestDatabase(t)
	name := fmt.Sprintf("registry_index_%d", time.Now().UnixNano())
	if _, err := admin.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{name}.Sanitize()); err != nil {
		t.Fatal(err)
	}
	config := admin.Config().Copy()
	config.Database = name
	conn, err := pgx.ConnectConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = conn.Close(context.Background())
		_, _ = admin.Exec(context.Background(), "DROP DATABASE "+pgx.Identifier{name}.Sanitize())
	})
	if changed, err := ensureRegistryLocationIndex(ctx, conn); err != nil || changed {
		t.Fatalf("missing table: %v %v", changed, err)
	}
	if _, err = conn.Exec(ctx, `CREATE TABLE location_name(name text,location_id integer);CREATE INDEX idx_location_name_lower_lookup ON location_name(name)`); err != nil {
		t.Fatal(err)
	}
	if changed, err := ensureRegistryLocationIndex(ctx, conn); err != nil || !changed {
		t.Fatalf("wrong-shaped index not repaired: %v %v", changed, err)
	}
	if changed, err := ensureRegistryLocationIndex(ctx, conn); err != nil || changed {
		t.Fatalf("exact index was rebuilt: %v %v", changed, err)
	}
	other, err := pgx.ConnectConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	defer other.Close(ctx)
	var acquired bool
	if err = other.QueryRow(ctx, "SELECT pg_try_advisory_lock(hashtext('jobseek:location-name-lower-lookup-index'))").Scan(&acquired); err != nil || !acquired {
		t.Fatalf("index lock leaked: %v %v", acquired, err)
	}
}

func TestRegistryPostgresRedisEndToEndAndFailureBoundary(t *testing.T) {
	ctx, conn, _ := registryTestDatabase(t)
	client := reaperRedisFixture(t, ctx)
	root := t.TempDir()
	data := filepath.Join(root, "data")
	for _, dir := range []string{data, filepath.Join(root, "src/shared"), filepath.Join(root, "go/typesense-exporter")} {
		if err := os.MkdirAll(dir, 0700); err != nil {
			t.Fatal(err)
		}
	}
	for _, name := range []string{"pyproject.toml", "src/shared/constants.py", "go/typesense-exporter/go.mod"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("fixture"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write := func(name, body string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(data, name+".csv"), []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write("companies", "slug,name\nfixture,Fixture\n")
	write("boards", "company_slug,board_slug,board_url,monitor_type\nfixture,fixture,https://fixture.invalid/jobs,rss\n")
	var schema string
	if err := conn.QueryRow(ctx, "SHOW search_path").Scan(&schema); err != nil {
		t.Fatal(err)
	}
	dsn, err := url.Parse(os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	query := dsn.Query()
	query.Set("search_path", schema)
	dsn.RawQuery = query.Encode()
	t.Setenv("LOCAL_DATABASE_URL", dsn.String())
	t.Setenv("TYPESENSE_OPERATIONS_KEY", "")
	t.Setenv("REDIS_URL", (&url.URL{Scheme: "unix", Path: client.Options().Addr}).String())
	if err = runRegistry([]string{"--source-data-dir", data}); err != nil {
		t.Fatal(err)
	}
	var id string
	if err = conn.QueryRow(ctx, "SELECT id::text FROM job_board WHERE board_slug='fixture'").Scan(&id); err != nil {
		t.Fatal(err)
	}
	if actual := client.HGet(ctx, "board:"+id, "board_url").Val(); actual != "https://fixture.invalid/jobs" {
		t.Fatalf("committed board was not published: %q", actual)
	}
	before, err := snapshotReaperKeys(ctx, client)
	if err != nil {
		t.Fatal(err)
	}
	write("companies", "slug,name\nfixture,Uncommitted\nbroken,\n")
	if err = runRegistry([]string{"--source-data-dir", data}); err == nil {
		t.Fatal("invalid local write accepted")
	}
	after, err := snapshotReaperKeys(ctx, client)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(before, after) {
		t.Fatal("failed local transaction published queue effects")
	}
	var name string
	if err = conn.QueryRow(ctx, "SELECT name FROM company WHERE slug='fixture'").Scan(&name); err != nil || name != "Fixture" {
		t.Fatalf("partial local write survived: %s %v", name, err)
	}
}
