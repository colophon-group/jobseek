package main

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestWorkerRouteCutoverPreservesSchedulesReversesAndRefusesBeforeMutation(t *testing.T) {
	for _, target := range workerRouteTargets {
		t.Run(target.Slug, func(t *testing.T) { testWorkerRouteTargetContract(t, target) })
	}
}

func testWorkerRouteTargetContract(t *testing.T, target workerRouteTarget) {
	ctx, conn, _ := registryTestDatabase(t)
	c := capacityRedisFixture(t, ctx)
	cfg := map[string]any{"scraper_type": "api_sniffer", "scraper_config": map[string]any{"api_url": target.APIURL}}
	if target.StaticConfigs != nil {
		cfg = map[string]any{"scraper_type": target.LegacyType, "scraper_config": target.StaticConfigs.Forward}
	}
	raw, _ := json.Marshal(cfg)
	var board, company string
	if err := conn.QueryRow(ctx, `INSERT INTO job_board(company_id,board_slug,board_url,scraper_needs_browser,metadata) VALUES(gen_random_uuid(),$1,$2,false,$3::jsonb) RETURNING id::text,company_id::text`, target.Slug, target.BoardURL, string(raw)).Scan(&board, &company); err != nil {
		t.Fatal(err)
	}
	ids := make([]string, 3)
	due := time.Date(2026, 10, 14, 8, 30, 1, 125000000, time.UTC)
	for i := range ids {
		url := fmt.Sprintf("https://%s/jobs/%d", target.Domain, i+1)
		if target.LegacyType == "dom" {
			url = fmt.Sprintf("https://%s/job/detail/JR%d", target.Domain, i+1)
		}
		if target.Slug == "venturelab-careers" {
			url = fmt.Sprintf("https://%s/jobs/_publicJob%d_/apply?language=en", target.Domain, i+1)
		}
		if target.StaticConfigs != nil {
			path := map[string]string{
				"mcdonalds-au-restaurants":         "/crew-member/job/%d",
				"bridgestone-careers-australia":    "/jobDetailsModern?selectJob=%d",
				"emirates-group-dnata-au":          "/jobDetailsModern?selectJob=%d",
				"emirates-group-dnata-catering-au": "/jobDetailsModern?selectJob=%d&s=250&modern=1",
				"wix-careers":                      "/position/REF%d",
				"cma-cgm-air-belgium":              "/jobs/first-officer-%d/",
				"mufg-trust-jp":                    "/u/job.phtml?job_code=%d",
			}[target.Slug]
			url = "https://" + target.Domain + fmt.Sprintf(path, i+1)
		}
		var hash any = "123"
		if i == 0 {
			hash = nil
		}
		if err := conn.QueryRow(ctx, `INSERT INTO job_posting(company_id,board_id,source_url,next_scrape_at,description_r2_hash) VALUES($1::uuid,$2::uuid,$3,$4,$5) RETURNING id::text`, company, board, url, due, hash).Scan(&ids[i]); err != nil {
			t.Fatal(err)
		}
		textHash := "456"
		if i == 0 {
			textHash = ""
		}
		cachedURL := url
		if target.Slug == "emirates-group-dnata-catering-au" && i != 1 {
			cachedURL = strings.Replace(url, "&s=250&modern=1", "&modern=1", 1)
		}
		c.HSet(ctx, "scrape:"+ids[i], "board_id", board, "source_url", cachedURL, "domain", target.Domain, "scrape_step", "0", "description_r2_hash", textHash, "retained", "original")
		key := "scrapes_browser:" + target.Domain
		score := float64(123 + i)
		if i == 0 {
			key = "ft_scrapes_browser:" + target.Domain
			score = 0
		}
		c.ZAdd(ctx, key, redis.Z{Member: ids[i], Score: score})
	}
	// A duplicate first-time promotion must stay first-time, retaining its score.
	c.ZAdd(ctx, "ft_scrapes_simple:"+target.Domain, redis.Z{Member: ids[1], Score: 7})
	c.Set(ctx, "ratelimit:"+target.Domain, "200", 0)
	c.ZAdd(ctx, "scrapes_browser:"+target.Domain, redis.Z{Member: "unrelated", Score: 9})
	c.ZAdd(ctx, "ready:rotation:browser", redis.Z{Member: target.Domain, Score: 300})
	c.ZAdd(ctx, "ready:browser:0", redis.Z{Member: target.Domain, Score: 1})
	// Every unsafe state must refuse the whole inventory, including earlier rows.
	guards := []struct {
		key   string
		value []any
	}{
		{"ordinary:ownership:active", []any{"owner", "go"}},
		{"lightpanda-b0:producer-owner", []any{"owner", "go"}},
		{"scrape:" + ids[2], []any{"scrape_step", "1"}},
		{"inflight_tokens:browser", []any{"scrape|" + target.Domain + "|" + ids[2], "lease"}},
	}
	for _, g := range guards {
		before := workerRouteSnapshot(t, c, ctx)
		c.HSet(ctx, g.key, g.value...)
		guarded := workerRouteSnapshot(t, c, ctx)
		if _, err := repairWorkerRouteCutover(ctx, conn, c); err == nil {
			t.Fatal("accepted unsafe state", g.key)
		} else if !strings.Contains(err.Error(), target.Slug) {
			t.Fatal("missing bounded target diagnostic", err)
		}
		if got := workerRouteSnapshot(t, c, ctx); got != guarded {
			t.Fatal("refusal changed queues", g.key)
		}
		if strings.HasPrefix(g.key, "scrape:") {
			c.HSet(ctx, g.key, "scrape_step", "0")
		} else {
			c.Del(ctx, g.key)
		}
		if workerRouteSnapshot(t, c, ctx) != before {
			t.Fatal("fixture did not restore")
		}
	}
	// A historical URL exception must not relax any other cache identity.
	for _, field := range []string{"source_url", "board_id", "domain"} {
		key := "scrape:" + ids[2]
		original := c.HGet(ctx, key, field).Val()
		c.HSet(ctx, key, field, "invalid-identity")
		before := workerRouteSnapshot(t, c, ctx)
		if _, err := repairWorkerRouteCutover(ctx, conn, c); err == nil || !strings.Contains(err.Error(), "worker route scrape identity or fallback step mismatch") {
			t.Fatal("accepted changed cache identity or lost safe diagnostic", field, err)
		}
		if workerRouteSnapshot(t, c, ctx) != before {
			t.Fatal("identity refusal mutated queues", field)
		}
		c.HSet(ctx, key, field, original)
	}
	result, err := repairWorkerRouteCutover(ctx, conn, c)
	if err != nil || result.Selected != 3 || result.Repaired != 3 {
		t.Fatal(result, err)
	}
	for i, id := range ids {
		key := "scrapes_simple:" + target.Domain
		score := float64(123 + i)
		if i < 2 {
			key = "ft_scrapes_simple:" + target.Domain
			score = 0
			if i == 1 {
				score = 7
			}
		}
		if got, err := c.ZScore(ctx, key, id).Result(); err != nil || got != score {
			t.Fatal("schedule changed", i, got, err)
		}
		if i > 0 && c.HGet(ctx, "scrape:"+id, "description_r2_hash").Val() != "456" {
			t.Fatal("lane repair overwrote existing lifecycle hash")
		}
		var canonicalURL string
		if err := conn.QueryRow(ctx, `SELECT source_url FROM job_posting WHERE id=$1::uuid`, id).Scan(&canonicalURL); err != nil {
			t.Fatal(err)
		}
		if c.HGet(ctx, "scrape:"+id, "source_url").Val() != canonicalURL {
			t.Fatal("cache URL not canonical after repair")
		}
		if c.HGet(ctx, "scrape:"+id, "retained").Val() != "original" {
			t.Fatal("cache field lost")
		}
	}
	if c.ZScore(ctx, "ready:browser:2", target.Domain).Val() != 300 || c.ZCard(ctx, "ready:browser:0").Val() != 0 {
		t.Fatal("opposite readiness changed")
	}
	again, err := repairWorkerRouteCutover(ctx, conn, c)
	if err != nil || again.Repaired != 0 {
		t.Fatal("retry changed routes", again, err)
	}
	// Complete rollback can reapply the same lane while a stopped worker's
	// unexpired attempt awaits the normal reaper. Preserve its exact token,
	// deadline and descriptor; do not enqueue a concurrent copy.
	member := "scrape|" + target.Domain + "|" + ids[2]
	c.ZRem(ctx, "scrapes_simple:"+target.Domain, ids[2])
	c.ZAdd(ctx, "inflight:simple", redis.Z{Member: member, Score: float64(time.Now().Unix() + 600)})
	c.HSet(ctx, "inflight_tokens:simple", member, strings.Repeat("a", 32))
	beforeLive := c.HGetAll(ctx, "scrape:"+ids[2]).Val()
	liveDeadline := c.ZScore(ctx, "inflight:simple", member).Val()
	if live, err := repairWorkerRouteCutover(ctx, conn, c); err != nil || live.Repaired != 0 || !reflect.DeepEqual(beforeLive, c.HGetAll(ctx, "scrape:"+ids[2]).Val()) || c.ZScore(ctx, "inflight:simple", member).Val() != liveDeadline || c.HGet(ctx, "inflight_tokens:simple", member).Val() != strings.Repeat("a", 32) || c.ZScore(ctx, "scrapes_simple:"+target.Domain, ids[2]).Err() != redis.Nil {
		t.Fatal("unchanged route lost compatible inflight attempt", err)
	}
	c.ZAdd(ctx, "scrapes_simple:"+target.Domain, redis.Z{Member: ids[2], Score: 125})
	duplicate := workerRouteSnapshot(t, c, ctx)
	if _, err := repairWorkerRouteCutover(ctx, conn, c); err == nil || workerRouteSnapshot(t, c, ctx) != duplicate {
		t.Fatal("inflight route accepted a queued duplicate")
	}
	c.ZRem(ctx, "inflight:simple", member)
	c.HDel(ctx, "inflight_tokens:simple", member)
	cfg = map[string]any{"scraper_type": target.LegacyType, "scraper_config": map[string]any{"fields": map[string]any{"description": "description"}}}
	if target.LegacyType == "dom" {
		cfg["scraper_config"] = map[string]any{"render": true, "steps": []any{map[string]any{"field": "description"}}}
	}
	if target.StaticConfigs != nil {
		cfg["scraper_config"] = target.StaticConfigs.Reverse
	}
	raw, _ = json.Marshal(cfg)
	if _, err := conn.Exec(ctx, `UPDATE job_board SET scraper_needs_browser=true,metadata=$1::jsonb WHERE id=$2::uuid`, string(raw), board); err != nil {
		t.Fatal(err)
	}
	reverse, err := repairWorkerRouteCutover(ctx, conn, c)
	if err != nil || reverse.Repaired != 3 || reverse.Worker != "browser" {
		t.Fatal("rollback failed", reverse, err)
	}
	var gotDue time.Time
	if err := conn.QueryRow(ctx, `SELECT next_scrape_at FROM job_posting WHERE id=$1::uuid`, ids[2]).Scan(&gotDue); err != nil || !gotDue.Equal(due) {
		t.Fatal("SQL due date changed", gotDue, err)
	}
}

func workerRouteSnapshot(t *testing.T, c *redis.Client, ctx context.Context) string {
	t.Helper()
	v, err := capacityKeySnapshot(ctx, c)
	if err != nil {
		t.Fatal(err)
	}
	// Long source URLs make Redis use a hash table. Read-only HGETs may
	// advance its rehash and change DUMP ordering without changing a field.
	// Compare every hash field canonically; retain exact dumps for other types.
	for key := range v {
		kind, err := c.Type(ctx, key).Result()
		if err != nil {
			t.Fatal(err)
		}
		if kind == "hash" {
			fields, err := c.HGetAll(ctx, key).Result()
			if err != nil {
				t.Fatal(err)
			}
			body, err := json.Marshal(fields)
			if err != nil {
				t.Fatal(err)
			}
			v[key] = "hash:" + string(body)
		}
	}
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestWorkerRoutePreviousURLIsMigration0031Only(t *testing.T) {
	canonical := "https://dnatacatering.expr3ss.com/jobDetailsModern?selectJob=123&s=250&modern=1"
	target := workerRouteTarget{Slug: "emirates-group-dnata-catering-au"}
	if got := workerRoutePreviousURL(target, canonical); got != "https://dnatacatering.expr3ss.com/jobDetailsModern?selectJob=123&modern=1" {
		t.Fatal(got)
	}
	for _, url := range []string{
		strings.Replace(canonical, "s=250", "s=251", 1), canonical + "&tracking=1",
		strings.Replace(canonical, "https:", "http:", 1), strings.Replace(canonical, "selectJob=123", "selectJob=other", 1),
		strings.Replace(canonical, "dnatacatering.expr3ss.com", "dnata.expr3ss.com", 1),
	} {
		if workerRoutePreviousURL(target, url) != url {
			t.Fatal("accepted nonmigration URL")
		}
	}
	if workerRoutePreviousURL(workerRouteTarget{Slug: "other"}, canonical) != canonical {
		t.Fatal("accepted another board")
	}
}

func TestWorkerRouteCutoverLargeInventoryValidatesAllRowsAndReverses(t *testing.T) {
	ctx, conn, _ := registryTestDatabase(t)
	c := capacityRedisFixture(t, ctx)
	target := workerRouteTargets[0]
	const count = 10025
	due := time.Date(2026, 10, 26, 8, 30, 1, 125000000, time.UTC)
	metadata, _ := json.Marshal(map[string]any{"scraper_type": "api_sniffer", "scraper_config": map[string]any{"api_url": target.APIURL}})
	var board, company string
	if err := conn.QueryRow(ctx, `INSERT INTO job_board(company_id,board_slug,board_url,scraper_needs_browser,metadata) VALUES(gen_random_uuid(),$1,$2,false,$3::jsonb) RETURNING id::text,company_id::text`, target.Slug, target.BoardURL, string(metadata)).Scan(&board, &company); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,next_scrape_at,description_r2_hash) SELECT ('10000000-0000-0000-0000-'||lpad(n::text,12,'0'))::uuid,$1::uuid,$2::uuid,'https://'||$3||'/job/detail/JR'||n::text,$4,123 FROM generate_series(1,$5::integer) n`, company, board, target.Domain, due, count); err != nil {
		t.Fatal(err)
	}
	last := fmt.Sprintf("10000000-0000-0000-0000-%012d", count)
	url := fmt.Sprintf("https://%s/job/detail/JR%d", target.Domain, count)
	// A bad final SQL row must refuse before any Redis effect, including all
	// valid earlier rows that exceeded the old 10,000-row ceiling.
	if _, err := conn.Exec(ctx, `UPDATE job_posting SET source_url='https://unrelated.example/job/1' WHERE id=$1::uuid`, last); err != nil {
		t.Fatal(err)
	}
	before := workerRouteSnapshot(t, c, ctx)
	if _, err := repairWorkerRouteTarget(ctx, conn, c, target); err == nil || !strings.Contains(err.Error(), "canonical posting identity mismatch") {
		t.Fatal("late invalid canonical source did not reject complete inventory", err)
	}
	if workerRouteSnapshot(t, c, ctx) != before {
		t.Fatal("late canonical failure mutated an earlier route")
	}
	if _, err := conn.Exec(ctx, `UPDATE job_posting SET source_url=$2 WHERE id=$1::uuid`, last, url); err != nil {
		t.Fatal(err)
	}
	// The unchanged Lua preflight must also refuse a bad final cached step
	// without enqueuing any of the 10,024 earlier valid SQL rows.
	c.HSet(ctx, "scrape:"+last, "board_id", board, "source_url", url, "domain", target.Domain, "scrape_step", "1", "description_r2_hash", "456", "retained", "original")
	before = workerRouteSnapshot(t, c, ctx)
	if _, err := repairWorkerRouteTarget(ctx, conn, c, target); err == nil {
		t.Fatal("late invalid Redis fallback step admitted")
	}
	if workerRouteSnapshot(t, c, ctx) != before {
		t.Fatal("late Redis preflight failure mutated an earlier route")
	}
	c.HSet(ctx, "scrape:"+last, "scrape_step", "0")
	got, err := repairWorkerRouteTarget(ctx, conn, c, target)
	if err != nil || got.Selected != count || got.Repaired != count || got.Worker != "simple" {
		t.Fatal("complete large forward inventory failed", got, err)
	}
	if c.ZCard(ctx, "scrapes_simple:"+target.Domain).Val() != count || c.ZCard(ctx, "scrapes_browser:"+target.Domain).Val() != 0 {
		t.Fatal("forward inventory truncated or wrong lane retained")
	}
	if c.HGet(ctx, "scrape:"+last, "description_r2_hash").Val() != "456" || c.HGet(ctx, "scrape:"+last, "retained").Val() != "original" {
		t.Fatal("large inventory replaced retained cache lifecycle fields")
	}
	if score, err := c.ZScore(ctx, "scrapes_simple:"+target.Domain, last).Result(); err != nil || score != float64(due.UnixMicro())/1e6 {
		t.Fatal("canonical recurring deadline changed", err)
	}
	metadata, _ = json.Marshal(map[string]any{"scraper_type": target.LegacyType, "scraper_config": map[string]any{"render": true}})
	if _, err := conn.Exec(ctx, `UPDATE job_board SET scraper_needs_browser=true,metadata=$2::jsonb WHERE id=$1::uuid`, board, string(metadata)); err != nil {
		t.Fatal(err)
	}
	got, err = repairWorkerRouteTarget(ctx, conn, c, target)
	if err != nil || got.Selected != count || got.Repaired != count || got.Worker != "browser" {
		t.Fatal("complete large rollback inventory failed", got, err)
	}
	if c.ZCard(ctx, "scrapes_browser:"+target.Domain).Val() != count || c.ZCard(ctx, "scrapes_simple:"+target.Domain).Val() != 0 {
		t.Fatal("large rollback truncated or left mixed lanes")
	}
	var actual int
	if err := conn.QueryRow(ctx, `SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND next_scrape_at=$2 AND is_active`, board, due).Scan(&actual); err != nil || actual != count {
		t.Fatal("route repair modified canonical schedules", err)
	}
}
