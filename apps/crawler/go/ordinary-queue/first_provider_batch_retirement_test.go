package queue

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/redis/go-redis/v9"
)

func firstProviderBatchFixture(t *testing.T, provider string) firstOwnerFixture {
	t.Helper()
	proxy := strings.HasSuffix(provider, "/proxy")
	provider = strings.Split(provider, "/")[0]
	p := firstOwnershipFixture(t)
	f, ctx := p.f, context.Background()
	board, metadata := "https://example.com/careers", `{"scraper_type":"eightfold"}`
	if provider == "mokahr" {
		board, metadata = "https://example.com/social-recruitment/fixture/123", `{"org_id":"fixture","site_id":123,"scraper_type":"mokahr","scraper_config":{"enrich":["description"]}}`
	}
	if provider == "almacareer" {
		board, metadata = "https://fixture.jobs.cz/", `{"scraper_type":"skip"}`
	}
	switch provider {
	case "beehire":
		board, metadata = "https://app.beehire.com/career/fixture", `{"scraper_type":"skip"}`
	case "hirehive":
		board, metadata = "https://fixture.hirehive.com/", `{"scraper_type":"skip"}`
	case "welcometothejungle":
		board, metadata = "https://www.welcometothejungle.com/fr/companies/fixture/jobs", `{"scraper_type":"skip"}`
	case "computrabajo":
		board, metadata = "https://fixture.pandape.infojobs.com.br/", `{"scraper_type":"json-ld"}`
	case "ycombinator":
		board, metadata = "https://www.ycombinator.com/companies/fixture/jobs", `{"scraper_type":"json-ld"}`
	case "earcu":
		board, metadata = "https://jobs.example.com/jobs/vacancy/find", `{ "scraper_type":"skip"}`
	case "cvwarehouse":
		board, metadata = "https://fixture.cvw.io/", `{ "scraper_type":"skip"}`
	case "woowa":
		board, metadata = "https://career.woowahan.com/", `{ "scraper_type":"skip"}`
	case "deel":
		board, metadata = "https://jobs.deel.com/fixture", `{"scraper_type":"skip"}`
	case "hibob":
		board, metadata = "https://fixture.careers.hibob.com/", `{"scraper_type":"skip"}`
	case "traffit":
		board, metadata = "https://fixture.traffit.com/career/", `{"scraper_type":"skip"}`
	case "recruiterbox":
		board, metadata = "https://fixture.recruiterbox.com/", `{"scraper_type":"json-ld"}`
	case "jobs_ch":
		board, metadata = "https://www.jobs.ch/de/firmen/123-fixture/", `{"scraper_type":"json-ld"}`
	case "manatal":
		board, metadata = "https://www.careers-page.com/fixture", `{"scraper_type":"skip"}`
	case "hrmos":
		board, metadata = "https://hrmos.co/pages/fixture/jobs", `{"scraper_type":"json-ld"}`
	case "adp":
		board, metadata = "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=01234567-89ab-cdef-0123-456789abcdef&ccId=19000101_000001&lang=en_US", `{"scraper_type":"adp","scraper_config":{"enrich":["description"]}}`
	case "cornerstone":
		board, metadata = "https://fixture.csod.com/ux/ats/careersite/4/home?c=fixture", `{"scraper_type":"skip"}`
	case "paylocity":
		board, metadata = "https://2000recruiting.paylocity.com/Recruiting/Jobs/All/fixture", `{"scraper_type":"paylocity","scraper_config":{"enrich":["description","employment_type","job_location_type"]}}`
	case "paycom":
		board, metadata = "https://www.paycomonline.net/v4/ats/web.php/portal/11111111111111111111111111111111/career-page", `{"scraper_type":"paycom","scraper_config":{"enrich":["title","description","locations","employment_type","job_location_type","date_posted","base_salary"]}}`
	case "rippling":
		board, metadata = "https://ats.rippling.com/fixture/jobs", `{"scraper_type":"rippling"}`
	case "comeet":
		board, metadata = "https://www.comeet.com/jobs/fixture/C6.001", `{"scraper_type":"skip"}`
	case "jobvite":
		board, metadata = "https://jobs.jobvite.com/fixture", `{"scraper_type":"json-ld"}`
	case "phenom":
		metadata = `{"sitemap_url":"https://example.com/sitemap.xml","scraper_type":"json-ld"}`
	case "sitemap":
		metadata = `{"sitemap_url":"https://example.com/jobs.xml","scraper_type":"json-ld"}`
	case "dom":
		metadata = `{"url_filter":"/jobs/","pagination":{"param_name":"page","max_pages":3},"url_transform":{"find":"\\?tracking=.*$","replace":""},"scraper_type":"json-ld"}`
	case "rss":
		metadata = `{"preset":"generic","feed_url":"https://example.com/feed","scraper_type":"skip"}`
	case "inline":
		metadata = `{"steps":[{"tag":"h2","field":"title"}],"fetch_urls":[{"url":"https://example.com/alternate","headers":{"X-No-Cache":"true"}}],"scraper_type":"skip"}`
	case "api_sniffer":
		metadata = `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title"},"scraper_type":"json-ld","scraper_config":{"enrich":["description"]}}`

	case "softgarden":
		board, metadata = "https://fixture.softgarden.io", `{"scraper_type":"json-ld"}`
	case "ukg":
		board, metadata = "https://recruiting.ultipro.com/ABC123/JobBoard/11111111-1111-1111-1111-111111111111", `{"scraper_type":"skip"}`
	case "bamboohr":
		board, metadata = "https://fixture.bamboohr.com/careers", `{"scraper_type":"skip"}`
	case "recruiter_co_kr":
		board, metadata = "https://fixture.recruiter.co.kr/career/home", `{"scraper_type":"skip"}`
	}
	if proxy {
		var md map[string]any
		if json.Unmarshal([]byte(metadata), &md) != nil {
			t.Fatal("invalid proxy fixture metadata")
		}
		md["proxy"] = true
		raw, _ := json.Marshal(md)
		metadata = string(raw)
	}
	if _, e := f.observer.Exec(ctx, "UPDATE job_board SET board_url=$2,crawler_type=$3,throttle_key=$3,metadata=$4::jsonb WHERE id=$1::uuid", f.task.ID, board, provider, metadata); e != nil {
		t.Fatal(e)
	}
	config := profileConfig()
	config["board_url"], config["crawler_type"], config["metadata"] = board, provider, metadata
	config["company_id"], config["board_slug"] = f.company, "ordinary-"+f.company
	config["domain"], config["throttle_key"] = provider, provider
	if e := f.client.redis.HSet(ctx, "board:"+f.task.ID, config).Err(); e != nil {
		t.Fatal(e)
	}
	if e := f.client.redis.Del(ctx, "monitors_simple:greenhouse", "ft_monitors_simple:greenhouse").Err(); e != nil {
		t.Fatal(e)
	}
	if e := f.client.redis.ZRem(ctx, "ready:simple:1", "greenhouse").Err(); e != nil {
		t.Fatal(e)
	}
	if e := f.client.redis.ZAdd(ctx, "monitors_simple:"+provider, redis.Z{Score: 1, Member: f.task.ID}).Err(); e != nil {
		t.Fatal(e)
	}
	if e := f.client.redis.ZAdd(ctx, "ready:simple:1", redis.Z{Score: 1, Member: provider}).Err(); e != nil {
		t.Fatal(e)
	}
	plan, e := f.authority.StageOwnership(ctx, strings.Repeat("a", 40), []string{f.task.ID}, nil)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	p.plan = plan
	p.f.task = &Task{Worker: Simple, Kind: Monitor, ID: f.task.ID, Domain: provider}
	return p
}

func assertEightfoldCanonicalWatermarkCached(t *testing.T, p firstOwnerFixture) {
	t.Helper()
	ctx := context.Background()
	var canonical []byte
	if e := p.f.observer.QueryRow(ctx, "SELECT metadata->'pcsx_watermark' FROM job_board WHERE id=$1::uuid", p.f.task.ID).Scan(&canonical); e != nil {
		t.Fatal(e)
	}
	raw, e := p.f.client.redis.HGet(ctx, "board:"+p.f.task.ID, "metadata").Result()
	if e != nil {
		t.Fatal(e)
	}
	var metadata map[string]any
	if json.Unmarshal([]byte(raw), &metadata) != nil {
		t.Fatal("cache metadata invalid")
	}
	var expected any
	if len(canonical) > 0 && json.Unmarshal(canonical, &expected) != nil {
		t.Fatal("canonical watermark invalid")
	}
	if !reflect.DeepEqual(metadata["pcsx_watermark"], expected) {
		t.Fatal("committed watermark lost during cache synchronization")
	}
}

func TestRealProviderBatchColdRetirementAndEightfoldWatermarkRecovery(t *testing.T) {
	for _, provider := range []string{"beehire", "hirehive", "welcometothejungle", "computrabajo", "computrabajo/proxy", "ycombinator", "earcu", "earcu/proxy", "cvwarehouse", "woowa", "deel", "hibob", "traffit", "manatal", "hrmos", "recruiterbox", "jobs_ch", "mokahr", "almacareer", "eightfold", "softgarden", "ukg", "bamboohr", "recruiter_co_kr", "dom", "rss", "inline", "api_sniffer", "comeet", "jobvite", "paycom", "rippling", "adp", "cornerstone", "paylocity", "paylocity/proxy", "dom/proxy", "api_sniffer/proxy", "inline/proxy", "sitemap/proxy", "eightfold/proxy", "phenom/proxy"} {
		modes := []string{"interrupted", "committed-before-ack", "changed-setting"}
		if strings.Split(provider, "/")[0] == "eightfold" {
			modes = append(modes, "reaped-before-ack", "recovered-ack", "settled-cold", "save-failure", "orphan-cache")
		}
		for _, mode := range modes {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				p := firstProviderBatchFixture(t, provider)
				a, claim := firstRetirementClaim(t, p)
				ctx := context.Background()
				var result *GreenhouseCycleResult
				if mode != "interrupted" && mode != "changed-setting" {
					cycle, e := a.BeginGreenhouseCycle(ctx, claim)
					if e != nil {
						t.Fatal(e)
					}
					summary := GreenhouseInventorySummary{}
					if strings.Split(provider, "/")[0] == "eightfold" {
						summary.MetadataUpdates = map[string]any{"pcsx_watermark": map[string]any{"max_ts": 123, "interval_days": 7, "auto_full_crawl": true, "enabled": true, "last_full_at": "2026-10-06T03:00:00.120000+00:00", "last_incremental_at": "2026-10-06T03:00:00.120000+00:00", "extra": map[string]any{"host": "example.com", "domain": "fixture"}}}
					}
					result, e = cycle.FinishSuccess(ctx, summary)
					if e != nil {
						t.Fatal(e)
					}
					if strings.Split(provider, "/")[0] == "eightfold" && strings.Contains(p.f.client.redis.HGet(ctx, "board:"+claim.task.ID, "metadata").Val(), "max_ts") {
						t.Fatal("watermark cache advanced before canonical settlement")
					}
				}
				if mode == "changed-setting" {
					if _, e := p.f.observer.Exec(ctx, "UPDATE job_board SET check_interval_minutes=120 WHERE id=$1::uuid", p.f.task.ID); e != nil {
						t.Fatal(e)
					}
					if e := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "check_interval_minutes", "120").Err(); e != nil {
						t.Fatal(e)
					}
					before := snapshot(t, p.f.client)
					if _, e := applyFirstFixture(t, p, true); !errors.Is(e, ErrAuthorityLost) {
						t.Fatal("operator drift admitted cold retirement", e)
					}
					if !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
						t.Fatal("rejected retirement mutated Redis")
					}
					return
				}
				if mode == "reaped-before-ack" || mode == "recovered-ack" {
					expire(t, p.f.client, &claim.task)
					if _, e := guardedReap(t, p.f); e != nil {
						t.Fatal(e)
					}
				}
				if mode == "recovered-ack" {
					replacement, e := OpenOwnedAuthority(ctx, p.f.dsn, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision())
					if e != nil {
						t.Fatal(e)
					}
					defer replacement.Close()
					recovered, e := replacement.Claim(ctx, Simple)
					if e != nil || recovered == nil || recovered.RecoveredReceipt() == nil {
						t.Fatal("committed watermark receipt did not recover", e)
					}
					if _, e := replacement.BeginGreenhouseCycle(ctx, recovered); !errors.Is(e, ErrConfiguration) {
						t.Fatal("recovered commit restarted a fetch")
					}
					if e := replacement.Settle(ctx, recovered, recovered.RecoveredReceipt()); e != nil {
						t.Fatal(e)
					}
					assertEightfoldCanonicalWatermarkCached(t, p)
				}
				if mode == "settled-cold" {
					if e := a.Settle(ctx, claim, result.Receipt); e != nil {
						t.Fatal(e)
					}
					assertEightfoldCanonicalWatermarkCached(t, p)
				}
				if mode == "orphan-cache" {
					if e := p.f.client.redis.ZRem(ctx, "inflight:simple", inflight(&claim.task)).Err(); e != nil {
						t.Fatal(e)
					}
					if e := p.f.client.redis.HDel(ctx, "inflight_tokens:simple", inflight(&claim.task)).Err(); e != nil {
						t.Fatal(e)
					}
				}
				canonical, due := coldCanonicalSnapshot(t, p.f), firstRetirementDue(t, p)
				if mode == "save-failure" {
					firstFixtureSave(t, p, false)
					if _, e := applyFirstFixture(t, p, true); !errors.Is(e, ErrObservation) {
						t.Fatal("failed SAVE reported a durable retirement", e)
					}
					if firstFixtureState(t, p) != "active" {
						t.Fatal("failed SAVE retired SQL owner")
					}
					firstFixtureSave(t, p, true)
				}
				if result, e := applyFirstFixture(t, p, true); e != nil || result.State != "retired" {
					t.Fatal(result, e)
				}
				if mode != "orphan-cache" {
					assertFirstRetirementSchedule(t, p, due)
				} else if p.f.client.redis.ZScore(ctx, "monitors_simple:eightfold", p.f.task.ID).Err() != redis.Nil {
					t.Fatal("cache repair resurrected orphan work")
				}
				if coldCanonicalSnapshot(t, p.f) != canonical {
					t.Fatal("retirement rewrote canonical output or receipt")
				}
				if strings.Split(provider, "/")[0] == "eightfold" {
					assertEightfoldCanonicalWatermarkCached(t, p)
				}
				if e := a.Heartbeat(ctx, claim); !errors.Is(e, ErrAuthorityLost) {
					t.Fatal("retired writer retained authority", e)
				}
				restartPublicationRedisWithoutSave(t, p.f.client)
				if strings.Split(provider, "/")[0] == "eightfold" {
					assertEightfoldCanonicalWatermarkCached(t, p)
				}
				if mode != "orphan-cache" {
					assertFirstRetirementSchedule(t, p, due)
				}
			})
		}
	}
}
