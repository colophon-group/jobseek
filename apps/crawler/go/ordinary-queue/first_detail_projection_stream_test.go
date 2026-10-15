package queue

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

type projectionStreamTrace struct {
	streams, readOnly atomic.Int64
	paged             atomic.Bool
}

func (s *projectionStreamTrace) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	sql := strings.ToLower(d.SQL)
	if strings.HasPrefix(sql, "begin") && strings.Contains(sql, "read only") {
		s.readOnly.Add(1)
	}
	if strings.Contains(sql, "p.description_r2_hash,b.scraper_needs_browser") {
		s.streams.Add(1)
		for _, forbidden := range []string{"order by", "limit", "for share"} {
			if strings.Contains(sql, forbidden) {
				s.paged.Store(true)
			}
		}
	}
	return ctx
}
func (*projectionStreamTrace) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

type projectionBatchHook struct {
	inspections atomic.Int64
	failed      atomic.Bool
	oversized   atomic.Bool
	failSecond  bool
}

func (*projectionBatchHook) DialHook(next redis.DialHook) redis.DialHook { return next }
func (*projectionBatchHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *projectionBatchHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		args := cmd.Args()
		if cmd.Name() == "eval" && len(args) == 5 && strings.Contains(args[1].(string), "invalid cold detail batch") && args[4] == "inspect" {
			var rows []canonicalDetailProjection
			if json.Unmarshal([]byte(args[3].(string)), &rows) != nil || len(rows) == 0 || len(rows) > coldDetailProjectionBatch {
				h.oversized.Store(true)
			}
			n := h.inspections.Add(1)
			if h.failSecond && n == 2 && h.failed.CompareAndSwap(false, true) {
				return errors.New("injected second batch inspection failure")
			}
		}
		return next(ctx, cmd)
	}
}

func TestRealColdDetailProjectionStreamPrefixIsContainedAndReplayable(t *testing.T) {
	p := projectionFixture(t, Simple, `{"scraper_type":"json-ld"}`)
	ctx, r := context.Background(), p.f.client.redis
	board := p.plan.document.Details[0].BoardID
	ids := make([]string, coldDetailProjectionBatch+1)
	for n := range ids {
		ids[n] = ordinaryID(t)
	}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=ANY($1::uuid[])", ids)
	})
	due := time.Now().UTC().Truncate(time.Microsecond).Add(-time.Minute)
	if _, err := p.f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales,last_seen_at,next_scrape_at)
 SELECT id,$2::uuid,$3::uuid,'https://example.com/job/'||id::text,ARRAY['Original'],ARRAY['en'],now(),$4::timestamptz FROM unnest($1::uuid[]) id`, ids, p.f.company, board, due); err != nil {
		t.Fatal(err)
	}
	for _, id := range ids {
		if _, err := p.f.client.EnqueueURLDetail(ctx, URLOnlyDetail{ID: id, BoardID: p.plan.document.Members[0].BoardID, URL: "https://example.com/job/" + id, Due: due, Browser: true}); err != nil {
			t.Fatal(err)
		}
	}
	trace := &projectionStreamTrace{}
	config := p.f.observer.Config()
	config.MaxConns = 2
	config.ConnConfig.Tracer = trace
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	hook := &projectionBatchHook{failSecond: true}
	r.AddHook(hook)
	run := func() error {
		bounded, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		return WithHostColdSQL(bounded, pool, hostSQLBindingFixture(), func(call context.Context, _ *HostColdSQL) error {
			return repairStagedDetailProjection(call, pool, p.f.client, p.plan)
		})
	}
	if err := run(); err == nil || !hook.failed.Load() || firstFixtureState(t, p) != "staged" || r.Exists(ctx, ownershipProjectionKey).Val() != 0 {
		t.Fatal("later failed batch granted ownership or did not exercise prefix", err)
	}
	repaired := r.ZCard(ctx, "ft_scrapes_simple:example.com").Val()
	if repaired < int64(coldDetailProjectionBatch) || repaired >= int64(len(ids)+1) {
		t.Fatal("first batch prefix not retained", repaired)
	}
	if err := run(); err != nil {
		t.Fatal("canonical prefix retry failed", err)
	}
	if trace.streams.Load() != 2 || trace.readOnly.Load() != 2 || trace.paged.Load() || hook.oversized.Load() || hook.inspections.Load() != 4 {
		t.Fatal("stream lost read-only bounded single-query behavior", trace.streams.Load(), trace.readOnly.Load(), hook.inspections.Load())
	}
	if firstFixtureState(t, p) != "staged" || r.Exists(ctx, ownershipProjectionKey).Val() != 0 {
		t.Fatal("repair itself granted ownership")
	}
	if r.ZCard(ctx, "ft_scrapes_browser:example.com").Val() != 0 || r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != int64(len(ids)+1) {
		t.Fatal("stream retry lost or duplicated detail")
	}
	var unchanged int
	if err := p.f.observer.QueryRow(ctx, `SELECT count(*) FROM job_posting WHERE id=ANY($1::uuid[]) AND next_scrape_at=$2 AND titles=ARRAY['Original'] AND description_r2_hash IS NULL AND leased_until IS NULL`, ids, due).Scan(&unchanged); err != nil || unchanged != len(ids) {
		t.Fatal("repair changed canonical rows/deadlines/leases", unchanged, err)
	}
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal("repaired stream could not be adopted", err)
	}
}

type projectionCancelHook struct {
	cancel context.CancelFunc
	used   atomic.Bool
}

func (*projectionCancelHook) DialHook(next redis.DialHook) redis.DialHook { return next }
func (*projectionCancelHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *projectionCancelHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		err := next(ctx, cmd)
		args := cmd.Args()
		if cmd.Name() == "eval" && len(args) == 5 && strings.Contains(args[1].(string), "invalid cold detail batch") && args[4] == "inspect" && h.used.CompareAndSwap(false, true) {
			h.cancel()
		}
		return err
	}
}
func TestRealColdDetailProjectionStreamCancellationBetweenInspectionAndEffects(t *testing.T) {
	p := projectionFixture(t, Simple, `{"scraper_type":"json-ld"}`)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	r := p.f.client.redis
	key := "scrape:" + p.f.task.ID
	oldBoard := p.plan.document.Members[0].BoardID
	if err := r.HSet(ctx, key, "board_id", oldBoard).Err(); err != nil {
		t.Fatal(err)
	}
	before := r.HGetAll(ctx, key).Val()
	h := &projectionCancelHook{cancel: cancel}
	r.AddHook(h)
	err := WithHostColdSQL(ctx, p.f.observer, hostSQLBindingFixture(), func(call context.Context, _ *HostColdSQL) error {
		return repairStagedDetailProjection(call, p.f.observer, p.f.client, p.plan)
	})
	if err == nil || !h.used.Load() || ctx.Err() == nil {
		t.Fatal("cancelled inspection continued", err)
	}
	after := r.HGetAll(context.Background(), key).Val()
	if !reflect.DeepEqual(before, after) || firstFixtureState(t, p) != "staged" || r.Exists(context.Background(), ownershipProjectionKey).Val() != 0 {
		t.Fatal("cancelled stream changed projections or ownership")
	}
}
