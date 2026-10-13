package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

func TestRealB0EpochUsesOriginalBarrierAndExactHighWater(t *testing.T) {
	dsn := os.Getenv("JOBSEEK_ORDINARY_QUEUE_TEST_DATABASE_URL")
	if dsn == "" {
		if os.Getenv("JOBSEEK_ORDINARY_QUEUE_REQUIRE_POSTGRES") == "1" {
			t.Fatal("required private migrated fixture absent")
		}
		t.Skip("requires private migrated fixture")
	}
	u, e := url.Parse(dsn)
	if e != nil || u.Hostname() != "127.0.0.1" && u.Hostname() != "localhost" || !strings.HasSuffix(u.Path, "_ordinary_worker_test") {
		t.Fatal("requires explicitly isolated epoch fixture")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 40*time.Second)
	defer cancel()
	config, e := providerCutoverPoolConfig(dsn, "deploy-b0-epoch-fixture")
	if e != nil {
		t.Fatal(e)
	}
	pool, e := pgxpool.NewWithConfig(ctx, config)
	if e != nil {
		t.Fatal("private database unavailable")
	}
	defer pool.Close()
	observer, e := pgxpool.New(ctx, dsn)
	if e != nil {
		t.Fatal("observer unavailable")
	}
	defer observer.Close()
	tx, e := observer.Begin(ctx)
	if e != nil {
		t.Fatal(e)
	}
	if _, e := tx.Exec(ctx, "SELECT pg_advisory_xact_lock_shared($1)", b0EpochBarrier); e != nil {
		t.Fatal(e)
	}
	call, stop := context.WithTimeout(ctx, 120*time.Millisecond)
	if epoch, e := reserveB0Epoch(call, pool); e == nil || epoch != 0 {
		t.Fatal("reservation escaped original shared routing barrier")
	}
	stop()
	if e := tx.Rollback(ctx); e != nil {
		t.Fatal(e)
	}
	var body bytes.Buffer
	if e := writeB0Epoch(ctx, pool, "reserve-epoch", "", &body); e != nil {
		t.Fatal(e)
	}
	var reserved struct {
		Epoch int64 `json:"routing_epoch"`
	}
	if json.Unmarshal(body.Bytes(), &reserved) != nil || reserved.Epoch < 1 {
		t.Fatal("original reserve reply ABI")
	}
	for _, expected := range []string{"", "0", "-1", "01", "+1", "1.0", "99999999999999", fmt.Sprint(reserved.Epoch - 1)} {
		body.Reset()
		if writeB0Epoch(ctx, pool, "attest-epoch", expected, &body) == nil || body.Len() != 0 {
			t.Fatal("invalid/stale high-water attested")
		}
	}
	body.Reset()
	if e := writeB0Epoch(ctx, pool, "attest-epoch", fmt.Sprint(reserved.Epoch), &body); e != nil {
		t.Fatal(e)
	}
	var attested map[string]any
	if json.Unmarshal(body.Bytes(), &attested) != nil || attested["current"] != true || attested["routing_epoch"] != float64(reserved.Epoch) {
		t.Fatal("original attestation reply ABI")
	}
	newer, e := reserveB0Epoch(ctx, pool)
	if e != nil || newer <= reserved.Epoch {
		t.Fatal("reserved value not strictly newer")
	}
	if attestB0Epoch(ctx, pool, reserved.Epoch) == nil || attestB0Epoch(ctx, pool, newer) != nil {
		t.Fatal("stale value accepted after later reservation")
	}
	var timeout, role string
	if e := pool.QueryRow(ctx, "SELECT current_setting('statement_timeout'),current_setting('application_name')").Scan(&timeout, &role); e != nil || timeout != "30s" || role != "jobseek:crawler:deploy-b0-epoch-fixture:local" {
		t.Fatal("original local connection guards changed")
	}
	if _, e := observer.Exec(ctx, "SELECT pg_advisory_xact_lock_shared($1)", b0EpochBarrier); e != nil {
		t.Fatal("reservation retained transaction barrier")
	}
	binary := filepath.Join(t.TempDir(), "go-typesense-exporter")
	if output, e := exec.CommandContext(ctx, "go", "build", "-o", binary, ".").CombinedOutput(); e != nil {
		t.Fatalf("native epoch CLI build failed: %s", output)
	}
	command := exec.CommandContext(ctx, binary, "--b0-epoch", "reserve-epoch")
	command.Env = []string{"LOCAL_DATABASE_URL=" + dsn, "CRAWLER_DB_ROLE=deploy-b0-epoch-fixture"}
	output, e := command.CombinedOutput()
	if e != nil || json.Unmarshal(output, &reserved) != nil || reserved.Epoch <= newer {
		t.Fatal("compiled one-off reservation contract")
	}
	command = exec.CommandContext(ctx, binary, "--b0-epoch", "attest-epoch")
	command.Env = []string{"LOCAL_DATABASE_URL=" + dsn, "CRAWLER_DB_ROLE=deploy-b0-epoch-fixture", "LIGHTPANDA_B0_ROUTING_EPOCH=" + fmt.Sprint(reserved.Epoch)}
	output, e = command.CombinedOutput()
	if e != nil || json.Unmarshal(output, &attested) != nil || attested["current"] != true {
		t.Fatal("compiled one-off attestation contract")
	}
}
