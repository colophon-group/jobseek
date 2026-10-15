package queue

import (
	"context"
	"math"
	"reflect"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func projectionCommandCalls(t *testing.T, r *redis.Client, name string) int64 {
	t.Helper()
	body, err := r.Info(context.Background(), "commandstats").Result()
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range strings.Split(body, "\n") {
		if !strings.HasPrefix(line, "cmdstat_"+name+":") {
			continue
		}
		value := strings.Split(strings.TrimPrefix(line, "cmdstat_"+name+":"), ",")[0]
		count, err := strconv.ParseInt(strings.TrimPrefix(value, "calls="), 10, 64)
		if err != nil {
			t.Fatal(err)
		}
		return count
	}
	return 0
}

func TestRealColdDetailProjectionCachesOnlyInvocationInvariantChecks(t *testing.T) {
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
	typesBefore, edgesBefore := projectionCommandCalls(t, r, "type"), projectionCommandCalls(t, r, "zrange")
	err := WithHostColdSQL(ctx, p.f.observer, hostSQLBindingFixture(), func(call context.Context, _ *HostColdSQL) error {
		return repairStagedDetailProjection(call, p.f.observer, p.f.client, p.plan)
	})
	if err != nil {
		t.Fatal(err)
	}
	types, edges := projectionCommandCalls(t, r, "type")-typesBefore, projectionCommandCalls(t, r, "zrange")-edgesBefore
	// Each posting's distinct hash still needs its own TYPE on inspect and apply.
	// Shared queues/lease maps/ready indices need validation once per invocation,
	// rather than dozens of repeated checks per row across the full fleet.
	if types < int64(2*len(ids)) || types > int64(2*(len(ids)+1)+512) || edges > 512 {
		t.Fatal("cold projection repeated invariant queue checks per posting", types, edges)
	}
	if r.ZCard(ctx, "ft_scrapes_browser:example.com").Val() != 0 || r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != int64(len(ids)+1) {
		t.Fatal("cached preflight lost or duplicated queue membership")
	}
	if firstFixtureState(t, p) != "staged" || r.Exists(ctx, ownershipProjectionKey).Val() != 0 {
		t.Fatal("repair granted ownership")
	}
}

type projectionBetweenCallsMutation struct {
	used   atomic.Bool
	mutate func(context.Context) error
}

func (*projectionBetweenCallsMutation) DialHook(next redis.DialHook) redis.DialHook { return next }
func (*projectionBetweenCallsMutation) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *projectionBetweenCallsMutation) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		err := next(ctx, cmd)
		args := cmd.Args()
		if err == nil && cmd.Name() == "eval" && len(args) == 5 && strings.Contains(args[1].(string), "invalid cold detail batch") && args[4] == "inspect" && h.used.CompareAndSwap(false, true) {
			return h.mutate(ctx)
		}
		return err
	}
}

func TestRealColdDetailProjectionRevalidatesSharedQueuesBeforeApply(t *testing.T) {
	for _, mode := range []string{"changed-type", "infinite-edge"} {
		t.Run(mode, func(t *testing.T) {
			p := projectionFixture(t, Simple, `{"scraper_type":"json-ld"}`)
			ctx, r := context.Background(), p.f.client.redis
			key := "scrape:" + p.f.task.ID
			if err := r.HSet(ctx, key, "board_id", p.plan.document.Members[0].BoardID).Err(); err != nil {
				t.Fatal(err)
			}
			before := r.HGetAll(ctx, key).Val()
			hook := &projectionBetweenCallsMutation{mutate: func(ctx context.Context) error {
				if mode == "changed-type" {
					if err := r.Del(ctx, "ready:rotation:browser").Err(); err != nil {
						return err
					}
					return r.Set(ctx, "ready:rotation:browser", "invalid", 0).Err()
				}
				return r.ZAdd(ctx, "monitors_browser:example.com", redis.Z{Score: math.Inf(1), Member: "unrelated"}).Err()
			}}
			r.AddHook(hook)
			if _, err := applyFirstFixture(t, p, false); err == nil || !hook.used.Load() {
				t.Fatal("cached inspect authorized an unsafe later apply", err)
			}
			if !reflect.DeepEqual(before, r.HGetAll(ctx, key).Val()) || firstFixtureState(t, p) != "staged" || r.Exists(ctx, ownershipProjectionKey).Val() != 0 {
				t.Fatal("failed apply changed row identity or granted ownership")
			}
		})
	}
}
