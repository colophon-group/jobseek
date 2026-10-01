package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5"
)

func TestRealNativeExecutableStagesAndInspectsWithoutSelectingOwnership(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx := context.Background()
	e := newNativeExecutableFixture(t, f, f.dsn)
	// Only the private fixture retires its initial synthetic owner and allocates
	// another epoch. The executable has neither operation nor SQL for activation.
	if _, err := f.pg.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE state='active'"); err != nil {
		t.Fatal(err)
	}
	var epoch int64
	if err := pgx.BeginFunc(ctx, f.pg, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", queue.OrdinaryLeaseBarrier); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(7544422533504811009)"); err != nil {
			return err
		}
		return tx.QueryRow(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')").Scan(&epoch)
	}); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(e.directory, "cohort.json")
	if err := os.WriteFile(file, []byte(`["`+f.board+`"]`), 0o600); err != nil {
		t.Fatal(err)
	}
	source := ordinaryFixtureSourceRevision(t)
	base := []string{"PATH=" + os.Getenv("PATH"), "LOCAL_DATABASE_URL=" + f.dsn, "REDIS_URL=unix://" + f.r.Options().Addr, "ORDINARY_OWNERSHIP_SOURCE_REVISION=" + source, "ORDINARY_OWNERSHIP_ROUTING_EPOCH=" + fmt.Sprint(epoch)}
	projection := f.r.Get(ctx, "ordinary:ownership:active").Val()
	snapshot := f.r.HGetAll(ctx, "board:"+f.board).Val()
	var boardBefore string
	if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(j)::text FROM job_board j WHERE id=$1::uuid", f.board).Scan(&boardBefore); err != nil {
		t.Fatal(err)
	}
	call := func(flag string, env []string, accepted bool) *OwnershipStageIdentity {
		t.Helper()
		command := exec.Command(e.binary, flag)
		command.Env = append(append([]string{}, base...), env...)
		output, err := command.CombinedOutput()
		if !accepted {
			if err == nil || !strings.Contains(string(output), "ordinary ownership") || strings.Contains(string(output), f.board) || strings.Contains(string(output), f.dsn) {
				t.Fatal("invalid native administrative operation accepted or exposed inputs")
			}
			return nil
		}
		var identity OwnershipStageIdentity
		if err != nil || json.Unmarshal(output, &identity) != nil || identity.Version != "jobseek.ordinary.stage-identity/v1" || identity.State != "staged" || identity.SourceRevision != source || identity.RoutingEpoch != epoch || identity.Members != 1 || !planPattern.MatchString(identity.PlanSHA256) || !sourcePattern.MatchString(identity.ProjectionSHA1) {
			t.Fatal("native staging/inspection lost exact document identity")
		}
		return &identity
	}
	stageEnv := []string{"ORDINARY_GO_WORKER_MODE=stage-ownership", "ORDINARY_GO_COHORT_FILE=" + file}
	first := call("--stage-ownership", stageEnv, true)
	repeat := call("--stage-ownership", stageEnv, true)
	if *first != *repeat {
		t.Fatal("native staging is not idempotent for exact source/configuration/epoch")
	}
	inspectEnv := []string{"ORDINARY_GO_WORKER_MODE=inspect-ownership", "ORDINARY_OWNERSHIP_PLAN_SHA256=" + first.PlanSHA256}
	if readback := call("--inspect-ownership", inspectEnv, true); *first != *readback {
		t.Fatal("native fresh readback changed staged identity")
	}
	call("--stage-ownership", append(stageEnv, "ORDINARY_OWNERSHIP_SOURCE_REVISION="+strings.Repeat("f", 40)), false)
	call("--inspect-ownership", append(inspectEnv, "ORDINARY_OWNERSHIP_PLAN_SHA256="+strings.Repeat("d", 64)), false)
	call("--inspect-ownership", append(inspectEnv, "ORDINARY_OWNERSHIP_ROUTING_EPOCH="+fmt.Sprint(epoch-1)), false)
	if _, err := f.pg.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", f.board); err != nil {
		t.Fatal(err)
	}
	call("--inspect-ownership", inspectEnv, false)
	if _, err := f.pg.Exec(ctx, "UPDATE job_board SET is_enabled=true WHERE id=$1::uuid", f.board); err != nil {
		t.Fatal(err)
	}
	if _, err := f.pg.Exec(ctx, "UPDATE job_board SET check_interval_minutes=61 WHERE id=$1::uuid", f.board); err != nil {
		t.Fatal(err)
	}
	if err := f.r.HSet(ctx, "board:"+f.board, "check_interval_minutes", "61").Err(); err != nil {
		t.Fatal(err)
	}
	call("--inspect-ownership", inspectEnv, false)
	if _, err := f.pg.Exec(ctx, "UPDATE job_board SET check_interval_minutes=60 WHERE id=$1::uuid", f.board); err != nil {
		t.Fatal(err)
	}
	if err := f.r.HSet(ctx, "board:"+f.board, "check_interval_minutes", "60").Err(); err != nil {
		t.Fatal(err)
	}
	var state, boardAfter string
	var current int64
	if err := f.pg.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", first.PlanSHA256).Scan(&state); err != nil || state != "staged" {
		t.Fatal("administrative commands selected ownership")
	}
	if err := f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&current); err != nil || current != epoch {
		t.Fatal("administrative command allocated/adopted another epoch")
	}
	if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(j)::text FROM job_board j WHERE id=$1::uuid", f.board).Scan(&boardAfter); err != nil || boardAfter != boardBefore {
		t.Fatal("staging changed canonical board state")
	}
	if f.r.Get(ctx, "ordinary:ownership:active").Val() != projection || !reflect.DeepEqual(f.r.HGetAll(ctx, "board:"+f.board).Val(), snapshot) || f.r.ZCard(ctx, "inflight:simple").Val() != 0 || f.r.ZScore(ctx, "monitors_simple:greenhouse", f.board).Val() != 1 {
		t.Fatal("native administrative command altered ownership/queue/configuration")
	}
	// Existing activation remains exclusively a fixture operation. Inspection
	// must decline active plans rather than treating them as reusable candidates.
	if _, err := f.pg.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256=$1", first.PlanSHA256); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.pg.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", first.PlanSHA256)
	})
	call("--inspect-ownership", inspectEnv, false)
}
