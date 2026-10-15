package queue

import (
	"context"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func projectionFixture(t *testing.T, worker WorkerType, metadata string) firstOwnerFixture {
	t.Helper()
	return firstIndependentDetailFixtureAtBoard(t, metadata, "https://example.com/job/REQ100", "example.com", "https://example.com/careers", worker)
}

func TestRealColdDetailProjectionRepairsLegacyIdentityAndLanes(t *testing.T) {
	for _, mode := range []string{"board", "source", "simple-to-browser", "browser-to-simple", "dual-lane", "old-domain"} {
		t.Run(mode, func(t *testing.T) {
			worker, metadata := Simple, `{"scraper_type":"json-ld"}`
			if mode == "browser-to-simple" {
				worker, metadata = Browser, `{"scraper_type":"json-ld","scraper_config":{"render":true}}`
			}
			p := projectionFixture(t, worker, metadata)
			ctx, client := context.Background(), p.f.client.redis
			id, board, key := p.f.task.ID, p.plan.document.Details[0].BoardID, "scrape:"+p.f.task.ID
			var due time.Time
			var hash *int64
			if err := p.f.observer.QueryRow(ctx, "SELECT next_scrape_at,description_r2_hash FROM job_posting WHERE id=$1::uuid", id).Scan(&due, &hash); err != nil {
				t.Fatal(err)
			}
			correct := "ft_scrapes_" + string(worker) + ":example.com"
			other := Simple
			if worker == Simple {
				other = Browser
			}
			if mode == "board" {
				client.HSet(ctx, key, "board_id", p.plan.document.Members[0].BoardID, "scrape_step", "1")
			}
			if mode == "source" || mode == "old-domain" {
				client.HSet(ctx, key, "source_url", "https://old.example.net/job/OLD", "egress_host", "old.example.net")
			}
			if mode == "simple-to-browser" || mode == "browser-to-simple" || mode == "dual-lane" {
				client.ZAdd(ctx, "ft_scrapes_"+string(other)+":example.com", redis.Z{Score: -7, Member: id})
				if mode != "dual-lane" {
					client.ZRem(ctx, correct, id)
				}
			}
			if mode == "old-domain" {
				client.HSet(ctx, key, "domain", "old.example.net")
				client.ZRem(ctx, correct, id)
				client.ZAdd(ctx, "ft_scrapes_"+string(worker)+":old.example.net", redis.Z{Score: -7, Member: id})
			}
			foreign := ordinaryID(t)
			client.ZAdd(ctx, "ft_scrapes_browser:example.com", redis.Z{Score: -17, Member: foreign})
			if _, err := applyFirstFixture(t, p, false); err != nil {
				t.Fatal("cold repair failed", err)
			}
			if client.HGet(ctx, key, "scrape_step").Val() != "0" || client.HGet(ctx, key, "board_id").Val() != board || client.HGet(ctx, key, "source_url").Val() != "https://example.com/job/REQ100" || client.HGet(ctx, key, "domain").Val() != "example.com" {
				t.Fatal("canonical identity was not restored")
			}
			score, err := client.ZScore(ctx, correct, id).Result()
			want := float64(0)
			if mode == "simple-to-browser" || mode == "browser-to-simple" || mode == "dual-lane" || mode == "old-domain" {
				want = -7
			}
			if err != nil || score != want || client.ZScore(ctx, "ft_scrapes_"+string(other)+":example.com", id).Err() != redis.Nil || client.ZScore(ctx, "ft_scrapes_browser:example.com", foreign).Val() != -17 {
				t.Fatal("first-time ordering or foreign membership changed", err)
			}
			if mode == "source" || mode == "old-domain" {
				if client.HExists(ctx, key, "egress_host").Val() {
					t.Fatal("old identity retained an egress attribution")
				}
			}
			var after time.Time
			var afterHash *int64
			if err := p.f.observer.QueryRow(ctx, "SELECT next_scrape_at,description_r2_hash FROM job_posting WHERE id=$1::uuid", id).Scan(&after, &afterHash); err != nil || !after.Equal(due) || !reflect.DeepEqual(hash, afterHash) {
				t.Fatal("repair changed canonical data or deadline", err)
			}
			if _, err := applyFirstFixture(t, p, false); err != nil || client.ZScore(ctx, correct, id).Val() != want {
				t.Fatal("exact retry changed repaired priority", err)
			}
			a, claim := firstRetirementClaim(t, p)
			if detail, err := a.ReadWorkdayDetail(ctx, claim); err != nil || detail.Profile().SourceURL != "https://example.com/job/REQ100" {
				t.Fatal("repaired task is not canonically claimable", err)
			}
		})
	}
}

func TestRealColdDetailProjectionRefusesLiveAndNativeLeases(t *testing.T) {
	for _, native := range []bool{false, true} {
		t.Run(map[bool]string{false: "live-legacy", true: "expired-native"}[native], func(t *testing.T) {
			p := projectionFixture(t, Simple, `{"scraper_type":"json-ld"}`)
			ctx, r := context.Background(), p.f.client.redis
			id, task := p.f.task.ID, "scrape|example.com|"+p.f.task.ID
			r.ZRem(ctx, "ft_scrapes_simple:example.com", id)
			deadline := float64(time.Now().Add(time.Minute).Unix())
			if native {
				deadline = float64(time.Now().Add(-time.Minute).Unix())
				r.HSet(ctx, "inflight_tokens:browser", task, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
			}
			r.ZAdd(ctx, "inflight:browser", redis.Z{Score: deadline, Member: task})
			before := r.HGetAll(ctx, "scrape:"+id).Val()
			if _, err := applyFirstFixture(t, p, false); err == nil || r.Exists(ctx, ownershipProjectionKey).Val() != 0 || r.ZScore(ctx, "inflight:browser", task).Val() != deadline || !reflect.DeepEqual(before, r.HGetAll(ctx, "scrape:"+id).Val()) {
				t.Fatal("live/native lease refusal had effects", err)
			}
		})
	}
}

func TestRealColdDetailProjectionRestoresExpiredLegacyWithoutRewritingSQLLease(t *testing.T) {
	p := projectionFixture(t, Simple, `{"scraper_type":"json-ld"}`)
	ctx, r := context.Background(), p.f.client.redis
	id, task := p.f.task.ID, "scrape|example.com|"+p.f.task.ID
	var lease time.Time
	if err := p.f.observer.QueryRow(ctx, "UPDATE job_posting SET leased_until=clock_timestamp()-interval '1 minute' WHERE id=$1::uuid RETURNING leased_until", id).Scan(&lease); err != nil {
		t.Fatal(err)
	}
	r.ZRem(ctx, "ft_scrapes_simple:example.com", id)
	r.ZAdd(ctx, "inflight:browser", redis.Z{Score: seconds(lease), Member: task})
	r.HSet(ctx, "inflight_strikes:browser", task, "2")
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal(err)
	}
	var due, afterLease time.Time
	if err := p.f.observer.QueryRow(ctx, "SELECT next_scrape_at,leased_until FROM job_posting WHERE id=$1::uuid", id).Scan(&due, &afterLease); err != nil || !lease.Equal(afterLease) {
		t.Fatal("canonical lease was rewritten", err)
	}
	if r.ZScore(ctx, "scrapes_simple:example.com", id).Val() != seconds(due) || r.ZScore(ctx, "inflight:browser", task).Err() != redis.Nil || r.HGet(ctx, "inflight_strikes:browser", task).Val() != "2" {
		t.Fatal("expired legacy deadline or strike conservation failed")
	}
}

func TestRealColdDetailProjectionPreservesValidFallbackProgress(t *testing.T) {
	p := projectionFixture(t, Simple, `{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}],"fallback":{"type":"json-ld"}}}`)
	ctx, r := context.Background(), p.f.client.redis
	id, key := p.f.task.ID, "scrape:"+p.f.task.ID
	r.HSet(ctx, key, "scrape_step", "1", "description_r2_hash", "123", "scrape_interval_hours", "12")
	r.ZRem(ctx, "ft_scrapes_simple:example.com", id)
	r.ZAdd(ctx, "ft_scrapes_browser:example.com", redis.Z{Score: -4, Member: id})
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal(err)
	}
	if r.HGet(ctx, key, "scrape_step").Val() != "1" || r.HGet(ctx, key, "scrape_interval_hours").Val() != "12" || r.HGet(ctx, key, "description_r2_hash").Val() != "123" || r.ZScore(ctx, "ft_scrapes_simple:example.com", id).Val() != -4 {
		t.Fatal("valid independent fallback progress changed")
	}
	a, claim := firstRetirementClaim(t, p)
	detail, err := a.ReadWorkdayDetail(ctx, claim)
	if err != nil || detail.Profile().Profile != jsonldDetailProfile {
		t.Fatal("repaired pending fallback selected another stage", err)
	}
}

func TestRealColdDetailProjectionSaveFailureCancellationLeavesCanonicalLegacyRoute(t *testing.T) {
	p := projectionFixture(t, Simple, `{"scraper_type":"json-ld"}`)
	ctx, r := context.Background(), p.f.client.redis
	id, key := p.f.task.ID, "scrape:"+p.f.task.ID
	r.HSet(ctx, key, "board_id", p.plan.document.Members[0].BoardID)
	r.ZRem(ctx, "ft_scrapes_simple:example.com", id)
	r.ZAdd(ctx, "ft_scrapes_browser:example.com", redis.Z{Score: -5, Member: id})
	firstFixtureSave(t, p, false)
	if _, err := applyFirstFixture(t, p, false); err == nil || firstFixtureState(t, p) != "staged" {
		t.Fatal("SAVE failure granted SQL ownership", err)
	}
	firstFixtureSave(t, p, true)
	if _, err := applyFirstFixture(t, p, true); err != nil {
		t.Fatal("supported staged cancellation failed", err)
	}
	if r.Exists(ctx, ownershipProjectionKey).Val() != 0 || r.HGet(ctx, key, "board_id").Val() != p.plan.document.Details[0].BoardID || r.ZScore(ctx, "ft_scrapes_simple:example.com", id).Val() != -5 || r.ZScore(ctx, "ft_scrapes_browser:example.com", id).Err() != redis.Nil {
		t.Fatal("cancellation did not preserve canonical legacy routing")
	}
	if legacy, err := p.f.client.Claim(ctx, Simple); err != nil || legacy == nil || legacy.ID != id {
		t.Fatal("cancelled repair stranded original legacy worker", err)
	}
}

func TestRealColdDetailProjectionRefusesUnboundScopeAndPlan(t *testing.T) {
	p := projectionFixture(t, Simple, `{"scraper_type":"json-ld"}`)
	if err := repairStagedDetailProjection(context.Background(), p.f.observer, p.f.client, p.plan); err == nil {
		t.Fatal("unscoped repair accepted")
	}
	changed := *p.plan
	changed.digest = strings.Repeat("b", 64)
	err := WithHostColdSQL(context.Background(), p.f.observer, hostSQLBindingFixture(), func(ctx context.Context, _ *HostColdSQL) error {
		return repairStagedDetailProjection(ctx, p.f.observer, p.f.client, &changed)
	})
	if err == nil || p.f.client.redis.Exists(context.Background(), ownershipProjectionKey).Val() != 0 {
		t.Fatal("unbound staged plan accepted")
	}
}

func TestRealColdDetailProjectionPagedPrefixIsContainedAndReplayable(t *testing.T) {
	p := projectionFixture(t, Simple, `{"scraper_type":"json-ld"}`)
	ctx, r := context.Background(), p.f.client.redis
	board := p.plan.document.Details[0].BoardID
	ids := make([]string, 0, 258)
	namespace := strings.ReplaceAll(ordinaryID(t), "-", "")[:10]
	middle := "-" + namespace[:4] + "-4" + namespace[4:7] + "-8" + namespace[7:10] + "-"
	for n := 0; n < 257; n++ {
		ids = append(ids, "00000001"+middle+fmt.Sprintf("%012d", n))
	}
	late := "ffffffff" + middle + "ffffffffffff"
	ids = append(ids, late)
	created := []string{}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=ANY($1::uuid[])", created)
	})
	for _, id := range ids {
		source := "https://example.com/job/" + id
		var due time.Time
		if err := p.f.observer.QueryRow(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales,last_seen_at,next_scrape_at)
 VALUES($1::uuid,$2::uuid,$3::uuid,$4,ARRAY['Original'],ARRAY['en'],now(),now()-interval '1 minute') RETURNING next_scrape_at`, id, p.f.company, board, source).Scan(&due); err != nil {
			t.Fatal(err)
		}
		created = append(created, id)
		oldBoard := p.plan.document.Members[0].BoardID
		if id == late {
			oldBoard = board
		}
		if _, err := p.f.client.EnqueueURLDetail(ctx, URLOnlyDetail{ID: id, BoardID: oldBoard, URL: source, Due: due, Browser: true}); err != nil {
			t.Fatal(err)
		}
	}
	r.HSet(ctx, "scrape:"+late, "scrape_step", "9")
	if _, err := applyFirstFixture(t, p, false); err == nil || firstFixtureState(t, p) != "staged" || r.Exists(ctx, ownershipProjectionKey).Val() != 0 {
		t.Fatal("later failed batch granted ownership", err)
	}
	if r.HGet(ctx, "scrape:"+ids[0], "board_id").Val() != board || r.ZScore(ctx, "ft_scrapes_simple:example.com", ids[0]).Val() != 0 || r.HGet(ctx, "scrape:"+late, "scrape_step").Val() != "9" {
		t.Fatal("valid prefix or refused later batch changed unexpectedly")
	}
	r.HSet(ctx, "scrape:"+late, "scrape_step", "0")
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal("canonical prefix retry failed", err)
	}
	if r.ZCard(ctx, "ft_scrapes_browser:example.com").Val() != 0 || r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != int64(len(ids)+1) {
		t.Fatal("paged retry lost or duplicated a detail")
	}
}
