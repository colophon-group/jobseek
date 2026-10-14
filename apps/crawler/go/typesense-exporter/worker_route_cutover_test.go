package main

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestWorkerRouteCutoverPreservesSchedulesReversesAndRefusesBeforeMutation(t *testing.T) {
	ctx, conn, _ := registryTestDatabase(t)
	c := capacityRedisFixture(t, ctx)
	cfg := map[string]any{"scraper_type": "api_sniffer", "scraper_config": map[string]any{"api_url": "https://bflcareers.peoplestrong.com/api/cp/rest/altone/cp/job/{id}/v2?part=basic,organisational,descriprion,workflow,skill,qualification,certification,language,applied&isReqId=false"}}
	raw, _ := json.Marshal(cfg)
	var board, company string
	if err := conn.QueryRow(ctx, `INSERT INTO job_board(company_id,board_slug,board_url,scraper_needs_browser,metadata) VALUES(gen_random_uuid(),$1,$2,false,$3::jsonb) RETURNING id::text,company_id::text`, bajajRouteBoard, "https://"+bajajRouteDomain+"/job/joblist", string(raw)).Scan(&board, &company); err != nil {
		t.Fatal(err)
	}
	ids := make([]string, 3)
	due := time.Date(2026, 10, 14, 8, 30, 1, 125000000, time.UTC)
	for i := range ids {
		url := fmt.Sprintf("https://%s/job/detail/JR%d", bajajRouteDomain, i+1)
		var hash any = "123"
		if i == 0 {
			hash = nil
		}
		if err := conn.QueryRow(ctx, `INSERT INTO job_posting(company_id,board_id,source_url,next_scrape_at,description_r2_hash) VALUES($1::uuid,$2::uuid,$3,$4,$5) RETURNING id::text`, company, board, url, due, hash).Scan(&ids[i]); err != nil {
			t.Fatal(err)
		}
		textHash := "123"
		if i == 0 {
			textHash = ""
		}
		c.HSet(ctx, "scrape:"+ids[i], "board_id", board, "source_url", url, "domain", bajajRouteDomain, "scrape_step", "0", "description_r2_hash", textHash, "retained", "original")
		key := "scrapes_browser:" + bajajRouteDomain
		score := float64(123 + i)
		if i == 0 {
			key = "ft_scrapes_browser:" + bajajRouteDomain
			score = 0
		}
		c.ZAdd(ctx, key, redis.Z{Member: ids[i], Score: score})
	}
	// A duplicate first-time promotion must stay first-time, retaining its score.
	c.ZAdd(ctx, "ft_scrapes_simple:"+bajajRouteDomain, redis.Z{Member: ids[1], Score: 7})
	c.Set(ctx, "ratelimit:"+bajajRouteDomain, "200", 0)
	c.ZAdd(ctx, "scrapes_browser:"+bajajRouteDomain, redis.Z{Member: "unrelated", Score: 9})
	c.ZAdd(ctx, "ready:rotation:browser", redis.Z{Member: bajajRouteDomain, Score: 300})
	c.ZAdd(ctx, "ready:browser:0", redis.Z{Member: bajajRouteDomain, Score: 1})
	// Every unsafe state must refuse the whole inventory, including earlier rows.
	guards := []struct {
		key   string
		value []any
	}{
		{"ordinary:ownership:active", []any{"owner", "go"}},
		{"lightpanda-b0:producer-owner", []any{"owner", "go"}},
		{"scrape:" + ids[2], []any{"scrape_step", "1"}},
		{"inflight_tokens:browser", []any{"scrape|" + bajajRouteDomain + "|" + ids[2], "lease"}},
	}
	for _, g := range guards {
		before := workerRouteSnapshot(t, c, ctx)
		c.HSet(ctx, g.key, g.value...)
		guarded := workerRouteSnapshot(t, c, ctx)
		if _, err := repairWorkerRouteCutover(ctx, conn, c); err == nil {
			t.Fatal("accepted unsafe state", g.key)
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
	result, err := repairWorkerRouteCutover(ctx, conn, c)
	if err != nil || result.Selected != 3 || result.Repaired != 3 {
		t.Fatal(result, err)
	}
	for i, id := range ids {
		key := "scrapes_simple:" + bajajRouteDomain
		score := float64(123 + i)
		if i < 2 {
			key = "ft_scrapes_simple:" + bajajRouteDomain
			score = 0
			if i == 1 {
				score = 7
			}
		}
		if got, err := c.ZScore(ctx, key, id).Result(); err != nil || got != score {
			t.Fatal("schedule changed", i, got, err)
		}
		if c.HGet(ctx, "scrape:"+id, "retained").Val() != "original" {
			t.Fatal("cache field lost")
		}
	}
	if c.ZScore(ctx, "ready:browser:2", bajajRouteDomain).Val() != 300 || c.ZCard(ctx, "ready:browser:0").Val() != 0 {
		t.Fatal("opposite readiness changed")
	}
	again, err := repairWorkerRouteCutover(ctx, conn, c)
	if err != nil || again.Repaired != 0 {
		t.Fatal("retry changed routes", again, err)
	}
	cfg = map[string]any{"scraper_type": "dom", "scraper_config": map[string]any{"render": true, "steps": []any{map[string]any{"field": "description"}}}}
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
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}
