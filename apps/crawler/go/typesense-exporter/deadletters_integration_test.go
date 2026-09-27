package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

func TestDeadlettersPostgresRedisReadOnly(t *testing.T) {
	dsn := os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("GO_TYPESENSE_TEST_DATABASE_URL is not set")
	}
	binary, err := exec.LookPath("redis-server")
	if err != nil {
		t.Fatal("redis-server is required for the configured database integration suite")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	// macOS temporary paths can exceed the Unix socket path limit.
	directory, err := os.MkdirTemp("/tmp", "jobseek-dl-")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(directory)
	socket := filepath.Join(directory, "redis.sock")
	command := exec.CommandContext(ctx, binary, "--port", "0", "--unixsocket", socket, "--save", "", "--appendonly", "no")
	command.Env = append(os.Environ(), "LC_ALL=C")
	command.Stdout, command.Stderr = os.Stdout, os.Stderr
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = command.Process.Kill(); _ = command.Wait() }()
	admin := redis.NewClient(&redis.Options{Network: "unix", Addr: socket, DisableIdentity: true})
	defer admin.Close()
	for {
		if admin.Ping(ctx).Err() == nil {
			break
		}
		if ctx.Err() != nil {
			t.Fatal("Redis did not start")
		}
		time.Sleep(20 * time.Millisecond)
	}
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(context.Background())
	schema := pgx.Identifier{fmt.Sprintf("deadletters_test_%d", time.Now().UnixNano())}.Sanitize()
	if _, err := conn.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		t.Fatal(err)
	}
	defer conn.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE")
	if _, err := conn.Exec(ctx, "SET search_path TO "+schema); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Exec(ctx, `CREATE TABLE job_board(id uuid PRIMARY KEY, board_slug text, board_url text, crawler_type text,board_status text,is_enabled bool,throttle_key text,monitor_needs_browser bool)`); err != nil {
		t.Fatal(err)
	}
	input, expected := deadletterOracle(t)
	for _, board := range input.Boards {
		if _, err := conn.Exec(ctx, `INSERT INTO job_board VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, board.ID, board.Slug, board.URL, board.Crawler, board.Status, board.Enabled, board.Domain, board.Browser); err != nil {
			t.Fatal(err)
		}
	}
	for _, entry := range input.Entries {
		if err := admin.ZAdd(ctx, "deadletter:"+entry.Wtype, redis.Z{Member: entry.Member, Score: entry.ReapedAt}).Err(); err != nil {
			t.Fatal(err)
		}
	}
	for id, config := range input.Configs {
		if len(config) > 0 {
			if err := admin.HSet(ctx, "board:"+id, config).Err(); err != nil {
				t.Fatal(err)
			}
		}
	}
	// Redis itself denies every write made by the inspector. PostgreSQL is
	// independently read-only inside a repeatable-read authority transaction.
	if err := admin.Do(ctx, "ACL", "SETUSER", "inspector", "on", ">fixture-pass", "~*", "+@read", "+hello", "+ping", "+client").Err(); err != nil {
		t.Fatal(err)
	}
	client := redis.NewClient(&redis.Options{Network: "unix", Addr: socket, Username: "inspector", Password: "fixture-pass", DisableIdentity: true})
	defer client.Close()
	config, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	config.MaxConns = 1
	config.ConnConfig.RuntimeParams["search_path"] = schema
	config.ConnConfig.RuntimeParams["default_transaction_read_only"] = "on"
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	snapshot, err := readDeadletterSnapshot(ctx, pool, client)
	if err != nil {
		t.Fatal(err)
	}
	result, err := classifyDeadletterSnapshot(snapshot)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(result, expected) {
		t.Fatal("real Redis/PostgreSQL output differs from Python oracle")
	}
	// Cross both 1000-element database and config pipeline boundaries.
	for i := 0; i < 1001; i++ {
		id := fmt.Sprintf("10000000-0000-0000-0000-%012d", i)
		if _, err := conn.Exec(ctx, `INSERT INTO job_board VALUES($1,'large','https://large.test','dom','active',true,'large.test',false)`, id); err != nil {
			t.Fatal(err)
		}
		if err := admin.ZAdd(ctx, "deadletter:simple", redis.Z{Member: "monitor|large.test|" + id, Score: float64(i)}).Err(); err != nil {
			t.Fatal(err)
		}
		if err := admin.HSet(ctx, "board:"+id, map[string]string{"domain": "large.test", "board_url": "https://large.test", "crawler_type": "dom"}).Err(); err != nil {
			t.Fatal(err)
		}
	}
	snapshot, err = readDeadletterSnapshot(ctx, pool, client)
	if err != nil {
		t.Fatal(err)
	}
	result, err = classifyDeadletterSnapshot(snapshot)
	if err != nil {
		t.Fatal(err)
	}
	if result.Total != expected.Total+1001 || result.Counts["simple"]["actionable"] != expected.Counts["simple"]["actionable"]+1001 {
		t.Fatalf("batch truncation: total %d counts %v", result.Total, result.Counts)
	}
	for _, lane := range []string{"simple", "browser"} {
		if admin.ZCard(ctx, "deadletter:"+lane).Val() != int64(lenByLane(result.Entries, lane)) {
			t.Fatal("inspector changed deadletter membership")
		}
	}
	if err := admin.Del(ctx, "board:"+input.Boards[0].ID).Err(); err != nil {
		t.Fatal(err)
	}
	if err := admin.Set(ctx, "board:"+input.Boards[0].ID, "wrong-type", 0).Err(); err != nil {
		t.Fatal(err)
	}
	if _, err := readDeadletterSnapshot(ctx, pool, client); err == nil {
		t.Fatal("corrupt Redis authority was silently classified")
	}
	exerciseDeadletterRecovery(t, ctx, conn, dsn, schema, admin, input)
	exerciseLiveLeaseReaper(t, ctx, pool, admin)
}
func lenByLane(entries []deadletterEntry, lane string) int {
	count := 0
	for _, entry := range entries {
		if entry.Wtype == lane {
			count++
		}
	}
	return count
}
