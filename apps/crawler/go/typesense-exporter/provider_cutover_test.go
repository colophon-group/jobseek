package main

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/redis/go-redis/v9"
)

func TestNativeNWProviderCutoverPreservesIdentityDeduplicatesAndRefusesForeignOwnership(t *testing.T) {
	ctx, conn, _ := registryTestDatabase(t)
	var board, company string
	if err := conn.QueryRow(ctx, `INSERT INTO job_board(company_id,board_slug,board_url) VALUES(gen_random_uuid(),'nw-careers','https://example.com/nw') RETURNING id::text,company_id::text`).Scan(&board, &company); err != nil {
		t.Fatal(err)
	}
	legacy := "https://jobs.nw-groupe.com/jobs/7465186-bess-project-manager-italy"
	canonical := "https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/bess-project-manager-italy_milano"
	var posting string
	if err := conn.QueryRow(ctx, `INSERT INTO job_posting(company_id,board_id,source_url,next_scrape_at) VALUES($1::uuid,$2::uuid,$3,now()) RETURNING id::text`, company, board, legacy).Scan(&posting); err != nil {
		t.Fatal(err)
	}
	if err := reapplyNWProviderCutover(ctx, conn); err != nil {
		t.Fatal(err)
	}
	var got string
	if err := conn.QueryRow(ctx, `SELECT source_url FROM job_posting WHERE id=$1::uuid`, posting).Scan(&got); err != nil || got != canonical {
		t.Fatal("native repair lost posting identity", err)
	}
	if _, err := conn.Exec(ctx, `INSERT INTO job_posting(company_id,board_id,source_url,next_scrape_at) VALUES($1::uuid,$2::uuid,$3,now())`, company, board, legacy); err != nil {
		t.Fatal(err)
	}
	if err := reapplyNWProviderCutover(ctx, conn); err != nil {
		t.Fatal(err)
	}
	var active bool
	if err := conn.QueryRow(ctx, `SELECT is_active FROM job_posting WHERE source_url=$1`, legacy).Scan(&active); err != nil || active {
		t.Fatal("legacy duplicate remained authoritative", err)
	}
	// Foreign canonical ownership must refuse the entire repair before changes.
	if _, err := conn.Exec(ctx, `UPDATE job_posting SET board_id=NULL WHERE id=$1::uuid`, posting); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Exec(ctx, `UPDATE job_posting SET is_active=true WHERE source_url=$1`, legacy); err != nil {
		t.Fatal(err)
	}
	if err := reapplyNWProviderCutover(ctx, conn); err == nil {
		t.Fatal("foreign canonical owner accepted")
	}
	if err := conn.QueryRow(ctx, `SELECT is_active FROM job_posting WHERE source_url=$1`, legacy).Scan(&active); err != nil || !active {
		t.Fatal("refusal changed legacy activity", err)
	}
}

func TestNativeUmantisCutoverResumesBatchesVerifiesReceiptsAndParksOnlyMonitors(t *testing.T) {
	ctx, conn, _ := registryTestDatabase(t)
	if _, err := conn.Exec(ctx, `CREATE TABLE crawler_identity_migration_receipt(migration_id text,board_id uuid REFERENCES job_board(id),version integer,completed_at timestamptz,migrated_count bigint,total_count bigint,PRIMARY KEY(migration_id,board_id))`); err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile("testdata/umantis_provider_contracts.json")
	if err != nil {
		t.Fatal(err)
	}
	var contracts [][4]string
	if err = json.Unmarshal(body, &contracts); err != nil || len(contracts) != 7 {
		t.Fatal("historical contracts missing", err)
	}
	var firstBoard, firstCompany string
	for _, row := range contracts {
		var company, board string
		if err = conn.QueryRow(ctx, `INSERT INTO company(slug,name) VALUES($1,$1) RETURNING id::text`, row[1]).Scan(&company); err != nil {
			t.Fatal(err)
		}
		if err = conn.QueryRow(ctx, `INSERT INTO job_board(company_id,board_slug,board_url,crawler_type,throttle_key) VALUES($1::uuid,$2,$3,'umantis',$2) RETURNING id::text`, company, row[0], row[2]).Scan(&board); err != nil {
			t.Fatal(err)
		}
		if row[0] == "bobst-global" {
			firstBoard, firstCompany = board, company
		}
	}
	client := reaperRedisFixture(t, ctx)
	ids := make([]string, 501)
	for i := range ids {
		url := fmt.Sprintf("https://recruitingapp-2882.umantis.com/Vacancies/%d/Description/2", i+1)
		if err = conn.QueryRow(ctx, `INSERT INTO job_posting(company_id,board_id,source_url,next_scrape_at) VALUES($1::uuid,$2::uuid,$3,now()) RETURNING id::text`, firstCompany, firstBoard, url).Scan(&ids[i]); err != nil {
			t.Fatal(err)
		}
		if err = client.HSet(ctx, "scrape:"+ids[i], "board_id", firstBoard, "source_url", url, "retained", "description").Err(); err != nil {
			t.Fatal(err)
		}
	}
	// Match the runtime's SQL ordering so the refusal occurs after batch one.
	rows, err := conn.Query(ctx, `SELECT id::text FROM job_posting ORDER BY id`)
	if err != nil {
		t.Fatal(err)
	}
	var ordered []string
	for rows.Next() {
		var id string
		if err = rows.Scan(&id); err != nil {
			t.Fatal(err)
		}
		ordered = append(ordered, id)
	}
	rows.Close()
	if rows.Err() != nil {
		t.Fatal(rows.Err())
	}
	last := ordered[500]
	if err = client.HSet(ctx, "scrape:"+last, "board_id", "foreign").Err(); err != nil {
		t.Fatal(err)
	}
	result, err := repairUmantisProviderCutover(ctx, conn, client, false)
	if err == nil || result.Changed != 500 {
		t.Fatal("later batch refusal lost resumable boundary", result, err)
	}
	if err = client.HSet(ctx, "scrape:"+last, "board_id", firstBoard).Err(); err != nil {
		t.Fatal(err)
	}
	result, err = repairUmantisProviderCutover(ctx, conn, client, false)
	if err != nil || result.Postings != 501 || result.Changed != 1 {
		t.Fatal("retry repeated earlier effects", result, err)
	}
	for _, id := range ids {
		hash, err := client.HGetAll(ctx, "scrape:"+id).Result()
		if err != nil || strings.HasSuffix(hash["source_url"], "/2") || hash["retained"] != "description" {
			t.Fatal("canonical hash or unrelated field lost", err)
		}
	}
	domain := "bobst-global"
	if err = client.ZAdd(ctx, "monitors_simple:"+domain, redis.Z{Member: firstBoard, Score: 1}).Err(); err != nil {
		t.Fatal(err)
	}
	if err = client.ZAdd(ctx, "scrapes_simple:"+domain, redis.Z{Member: ids[0], Score: 2}).Err(); err != nil {
		t.Fatal(err)
	}
	result, err = repairUmantisProviderCutover(ctx, conn, client, true)
	if err != nil || result.Changed != 0 || result.Parked != 1 {
		t.Fatal("parking replay changed canonical effects", result, err)
	}
	if n := client.ZCard(ctx, "scrapes_simple:"+domain).Val(); n != 1 {
		t.Fatal("rollback removed scrape work")
	}
	// Deleted/tampered durable receipt refuses before any Redis effect.
	if _, err = conn.Exec(ctx, `UPDATE job_board SET metadata=metadata-'_identity_migration_receipt' WHERE id=$1::uuid`, firstBoard); err != nil {
		t.Fatal(err)
	}
	before, err := snapshotReaperKeys(ctx, client)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = repairUmantisProviderCutover(ctx, conn, client, false); err == nil {
		t.Fatal("missing receipt accepted")
	}
	after, err := snapshotReaperKeys(ctx, client)
	if err != nil {
		t.Fatal(err)
	}
	a, _ := json.Marshal(before)
	b, _ := json.Marshal(after)
	if string(a) != string(b) {
		t.Fatal("receipt refusal changed Redis")
	}
}

func TestProviderCutoverPoolBudgetAndSessionGuards(t *testing.T) {
	c, err := providerCutoverPoolConfig("postgresql://fixture@localhost/fixture?pool_max_conns=99", "deploy-umantis-identity-cutover")
	if err != nil || c.MinConns != 0 || c.MaxConns != 1 || c.ConnConfig.RuntimeParams["statement_timeout"] != "30s" || c.ConnConfig.RuntimeParams["idle_in_transaction_session_timeout"] != "60s" {
		t.Fatal("provider session guards changed", err)
	}
	if _, err = providerCutoverPoolConfig("postgresql://fixture@localhost/fixture", "role\nsecret"); err == nil {
		t.Fatal("invalid role accepted")
	}
}

func TestCompiledProviderCutoverCommands(t *testing.T) {
	ctx, conn, _ := registryTestDatabase(t)
	if _, err := conn.Exec(ctx, `CREATE TABLE crawler_identity_migration_receipt(migration_id text,board_id uuid REFERENCES job_board(id),version integer,completed_at timestamptz,migrated_count bigint,total_count bigint,PRIMARY KEY(migration_id,board_id))`); err != nil {
		t.Fatal(err)
	}
	var schema string
	if err := conn.QueryRow(ctx, "SELECT current_schema()").Scan(&schema); err != nil {
		t.Fatal(err)
	}
	dsn, err := url.Parse(os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	query := dsn.Query()
	query.Set("search_path", schema)
	dsn.RawQuery = query.Encode()
	client := reaperRedisFixture(t, ctx)
	binary := filepath.Join(t.TempDir(), "go-typesense-exporter")
	build := exec.CommandContext(ctx, "go", "build", "-o", binary, ".")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("native command build: %v %s", err, output)
	}
	for _, args := range [][]string{{"--repair-nw-provider-cutover"}, {"--repair-umantis-identity-cutover"}, {"--repair-umantis-identity-cutover", "--park-monitors"}} {
		command := exec.CommandContext(ctx, binary, args...)
		command.Env = append(os.Environ(), "LOCAL_DATABASE_URL="+dsn.String(), "REDIS_URL=unix://"+client.Options().Addr, "CRAWLER_DB_ROLE=deploy-provider-fixture")
		output, err := command.CombinedOutput()
		if err != nil {
			t.Fatalf("compiled native repair refused fixture: %v %s", err, output)
		}
		if args[0] == "--repair-umantis-identity-cutover" {
			var result umantisCutoverSummary
			if json.Unmarshal(output, &result) != nil || result != (umantisCutoverSummary{}) {
				t.Fatal("native empty-database summary changed")
			}
		}
	}
	command := exec.CommandContext(ctx, binary, "--repair-nw-provider-cutover", "--park-monitors")
	command.Env = []string{}
	if err := command.Run(); err == nil || command.ProcessState.ExitCode() != 2 {
		t.Fatal("unbounded CLI argument accepted")
	}
}
