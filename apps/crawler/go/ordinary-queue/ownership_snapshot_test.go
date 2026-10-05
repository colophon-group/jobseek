package queue

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

type snapshotQueryTrace struct{ boards atomic.Int64 }

func (c *snapshotQueryTrace) TraceQueryStart(ctx context.Context, _ *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	if strings.Contains(data.SQL, "FROM public.job_board") {
		c.boards.Add(1)
	}
	return ctx
}
func (*snapshotQueryTrace) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

type snapshotPipelineTrace struct{ calls atomic.Int64 }

func (*snapshotPipelineTrace) DialHook(next redis.DialHook) redis.DialHook          { return next }
func (*snapshotPipelineTrace) ProcessHook(next redis.ProcessHook) redis.ProcessHook { return next }
func (c *snapshotPipelineTrace) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(ctx context.Context, commands []redis.Cmder) error {
		for _, command := range commands {
			if command.Name() == "hgetall" {
				c.calls.Add(1)
				break
			}
		}
		return next(ctx, commands)
	}
}

func TestRealOwnershipSnapshotBatchesAndPreservesCanonicalCachePairs(t *testing.T) {
	f := realAuthority(t, Monitor, Simple)
	ctx := context.Background()
	ids := []string{f.task.ID}
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=ANY($1::uuid[])", ids[1:])
	})
	for n := 0; n < ownershipSnapshotBatch; n++ {
		id := ordinaryID(t)
		ids = append(ids, id)
		if _, err := f.observer.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,throttle_key)
 VALUES($1::uuid,$2::uuid,$3,$4,'greenhouse','{}'::jsonb,'greenhouse')`, id, f.company, "snapshot-"+id, "https://boards.greenhouse.io/snapshot-"+id); err != nil {
			t.Fatal(err)
		}
		if err := f.client.redis.HSet(ctx, "board:"+id, map[string]string{"metadata": "{}", "board_slug": "snapshot-" + id, "cache_observation": "retained"}).Err(); err != nil {
			t.Fatal(err)
		}
	}

	trace := &snapshotQueryTrace{}
	config := f.authority.pool.Config()
	config.ConnConfig.Tracer = trace
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	a := &Authority{queue: f.client, pool: pool, epoch: f.epoch}
	pipelines := &snapshotPipelineTrace{}
	f.client.redis.AddHook(pipelines)
	err = a.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		// Duplicate monitor/detail membership must share one locked observation.
		pairs, err := a.observeOwnershipConfigs(ctx, tx, append(ids, ids[0]), false)
		if err != nil {
			return err
		}
		if len(pairs) != len(ids) || trace.boards.Load() != 1 || pipelines.calls.Load() != 2 {
			t.Fatal("fleet snapshot retained per-board network reads", len(pairs), trace.boards.Load(), pipelines.calls.Load())
		}
		for _, id := range []string{ids[0], ids[len(ids)-1]} {
			canonical, cached, err := a.observeBoardConfigsState(ctx, tx, id, false)
			if err != nil {
				return err
			}
			if !reflect.DeepEqual(pairs[id].canonical, canonical) || !reflect.DeepEqual(pairs[id].cached, cached) {
				t.Fatal("batch altered canonical or cache evidence")
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestRealOwnershipSnapshotRetainsEnabledAndRetirementRules(t *testing.T) {
	f := realAuthority(t, Monitor, Simple)
	ctx := context.Background()
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	err := f.authority.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		if _, err := f.authority.observeOwnershipConfigs(ctx, tx, []string{f.task.ID}, false); !errors.Is(err, ErrUnsupportedProfile) {
			t.Fatal("disabled board admitted")
		}
		pairs, err := f.authority.observeOwnershipConfigs(ctx, tx, []string{f.task.ID}, true)
		if err != nil || len(pairs) != 1 {
			t.Fatal("disabled member cannot retire", err)
		}
		if _, err := f.authority.observeOwnershipConfigs(ctx, tx, []string{f.task.ID, ordinaryID(t)}, true); !errors.Is(err, ErrUnsupportedProfile) {
			t.Fatal("missing canonical board silently omitted")
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}
