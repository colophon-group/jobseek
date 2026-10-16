package queue

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"reflect"
	"runtime"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func batchDetailIntents(t *testing.T, n int) (authorityFixture, *GreenhouseCycle, []URLOnlyDetail) {
	t.Helper()
	f, a, claim := urlOnlyDetailClaimFixture(t)
	ctx := context.Background()
	cycle, e := a.BeginGreenhouseCycle(ctx, claim)
	if e != nil {
		t.Fatal(e)
	}
	urls := []string{}
	for i := 0; i < n; i++ {
		urls = append(urls, "https://fixture.invalid/jobs/"+ordinaryID(t))
	}
	r, e := cycle.WriteURLOnlyBatch(ctx, urls)
	if e != nil || len(r.Details) != n {
		t.Fatal("committed intents absent", e)
	}
	return f, cycle, r.Details
}

func detailPublicationState(t *testing.T, f authorityFixture, details []URLOnlyDetail) map[string]any {
	t.Helper()
	ctx := context.Background()
	state := map[string]any{}
	domains := map[string]bool{}
	for _, d := range details {
		state[d.ID] = f.client.redis.HGetAll(ctx, "scrape:"+d.ID).Val()
		u, err := url.Parse(d.URL)
		if err != nil {
			t.Fatal(err)
		}
		domains[u.Hostname()] = true
	}
	for domain := range domains {
		for _, w := range []string{"simple", "browser"} {
			for _, prefix := range []string{"ft_scrapes_", "scrapes_"} {
				key := prefix + w + ":" + domain
				state[key] = f.client.redis.ZRangeWithScores(ctx, key, 0, -1).Val()
			}
		}
	}
	return state
}

func clearDetailPublications(t *testing.T, f authorityFixture, details []URLOnlyDetail) {
	t.Helper()
	keys := []string{"ft_scrapes_simple:fixture.invalid", "ft_scrapes_browser:fixture.invalid", "scrapes_simple:fixture.invalid", "scrapes_browser:fixture.invalid"}
	for _, d := range details {
		keys = append(keys, "scrape:"+d.ID)
	}
	if e := f.client.redis.Del(context.Background(), keys...).Err(); e != nil {
		t.Fatal(e)
	}
}

func TestRealDetailPublicationSurvivesDescriptionUpload(t *testing.T) {
	for _, batch := range []bool{false, true} {
		for _, firstTime := range []bool{false, true} {
			t.Run(fmt.Sprintf("batch=%t/first-time=%t", batch, firstTime), func(t *testing.T) {
				f, cycle, details := batchDetailIntents(t, 1)
				ctx := context.Background()
				intent := &details[0]
				if !firstTime {
					hash := int64(12)
					intent.DescriptionHash = &hash
					if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET description_r2_hash=12 WHERE id=$1::uuid", intent.ID); err != nil {
						t.Fatal(err)
					}
				}
				// The R2 drain changes content authority without changing the
				// pending enrichment deadline between commit and publication.
				if _, err := f.observer.Exec(ctx, `UPDATE job_posting SET description_r2_hash=99,to_be_enriched=true,
 updated_at=CASE WHEN description_r2_hash IS DISTINCT FROM 99 THEN now() ELSE updated_at END WHERE id=$1::uuid`, intent.ID); err != nil {
					t.Fatal(err)
				}
				if batch {
					if n, err := cycle.EnqueueURLDetails(ctx, details); err != nil || n != 1 {
						t.Fatal("upload discarded scheduled batch enrichment", n, err)
					}
				} else if added, err := cycle.EnqueueURLDetail(ctx, *intent); err != nil || !added {
					t.Fatal("upload discarded scheduled enrichment", err)
				}
				if hash, err := f.client.redis.HGet(ctx, "scrape:"+intent.ID, "description_r2_hash").Result(); err != nil || hash != "99" {
					t.Fatal("publication lost current uploaded content hash", err)
				}
				lane, other := "scrapes_simple:fixture.invalid", "ft_scrapes_simple:fixture.invalid"
				wantScore := seconds(intent.Due)
				if firstTime {
					lane, other = other, lane
					wantScore = 0
				}
				if score, err := f.client.redis.ZScore(ctx, lane, intent.ID).Result(); err != nil || score != wantScore {
					t.Fatal("upload changed publication priority or deadline", err)
				}
				if _, err := f.client.redis.ZScore(ctx, other, intent.ID).Result(); !errors.Is(err, redis.Nil) {
					t.Fatal("upload duplicated publication across tiers", err)
				}
			})
		}
	}
}

func TestRealDetailBatchMatchesOriginalPublicationAndStaleIntents(t *testing.T) {
	for _, mode := range []string{"current", "changed-due", "changed-hash", "inactive", "foreign-browser", "duplicate"} {
		t.Run(mode, func(t *testing.T) {
			f, c, details := batchDetailIntents(t, 4)
			ctx := context.Background()
			d := details[2]
			switch mode {
			case "changed-due":
				_, e := f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=next_scrape_at+interval '1 day' WHERE id=$1::uuid", d.ID)
				if e != nil {
					t.Fatal(e)
				}
			case "changed-hash":
				_, e := f.observer.Exec(ctx, "UPDATE job_posting SET description_r2_hash=99 WHERE id=$1::uuid", d.ID)
				if e != nil {
					t.Fatal(e)
				}
			case "inactive":
				_, e := f.observer.Exec(ctx, "UPDATE job_posting SET is_active=false WHERE id=$1::uuid", d.ID)
				if e != nil {
					t.Fatal(e)
				}
			case "foreign-browser":
				board := ordinaryID(t)
				if _, e := f.observer.Exec(ctx, "INSERT INTO job_board(id,company_id,board_slug,board_url,scraper_needs_browser) VALUES($1::uuid,$2::uuid,$3,$4,true)", board, f.company, "foreign-"+board, "https://fixture.invalid/"+board); e != nil {
					t.Fatal(e)
				}
				t.Cleanup(func() {
					_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE board_id=$1::uuid", board)
					_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", board)
				})
				if _, e := f.observer.Exec(ctx, "UPDATE job_posting SET board_id=$2::uuid WHERE id=$1::uuid", d.ID, board); e != nil {
					t.Fatal(e)
				}
				if e := f.client.redis.HSet(ctx, "board:"+board, "board_slug", "foreign-"+board).Err(); e != nil {
					t.Fatal(e)
				}
				details[2].BoardID = board
			case "duplicate":
				details = append(details, details[2])
			}
			original := 0
			for _, d := range details {
				added, e := c.EnqueueURLDetail(ctx, d)
				if e != nil {
					t.Fatal("single oracle", e)
				}
				if added {
					original++
				}
			}
			expected := detailPublicationState(t, f, details)
			clearDetailPublications(t, f, details)
			actual, e := c.EnqueueURLDetails(ctx, details)
			if e != nil || actual != original || !reflect.DeepEqual(detailPublicationState(t, f, details), expected) {
				t.Fatal("original canonical cache/lane/score/step publication differs", e, actual, original)
			}
		})
	}
}

func TestRealDetailBatchFenceAndAmbiguousPrefix(t *testing.T) {
	for _, mode := range []string{"invalid", "too-many", "changed-config", "changed-projection", "expired-lease", "publisher", "late-corrupt-queue"} {
		t.Run(mode, func(t *testing.T) {
			f, c, details := batchDetailIntents(t, 3)
			ctx := context.Background()
			switch mode {
			case "invalid":
				details[2].ID = "invalid"
			case "too-many":
				for len(details) <= URLDetailPublicationBatch {
					details = append(details, details[0])
				}
			case "changed-config":
				if e := f.client.redis.HSet(ctx, "board:"+f.task.ID, "board_url", "https://fixture.invalid/changed").Err(); e != nil {
					t.Fatal(e)
				}
			case "changed-projection":
				if e := f.client.redis.Set(ctx, ownershipProjectionKey, "foreign", 0).Err(); e != nil {
					t.Fatal(e)
				}
			case "expired-lease":
				if _, e := f.client.redis.ZAdd(ctx, "inflight:"+string(c.claim.task.Worker), redis.Z{Score: float64(time.Now().Unix() - 1), Member: inflight(&c.claim.task)}).Result(); e != nil {
					t.Fatal(e)
				}
			case "publisher":
				if _, e := f.observer.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", f.task.ID); e != nil {
					t.Fatal(e)
				}
			case "late-corrupt-queue":
				// Corrupt only the last intent's cache. The earlier original Lua writes
				// remain valid and must survive a later rejected publication.
				if e := f.client.redis.Set(ctx, "scrape:"+details[2].ID, "wrong-type", 0).Err(); e != nil {
					t.Fatal(e)
				}
			}
			added, e := c.EnqueueURLDetails(ctx, details)
			if e == nil {
				t.Fatal("unsafe batch accepted", mode)
			}
			if mode != "late-corrupt-queue" && added != 0 {
				t.Fatal("fenced batch published before validation", added)
			}
			if mode == "late-corrupt-queue" {
				if added != 2 || len(f.client.redis.HGetAll(ctx, "scrape:"+details[0].ID).Val()) == 0 {
					t.Fatal("valid prefix lost", added)
				}
			}
		})
	}
}

func TestRealDetailBatchBarrierWaitCannotPublishFromExpiredContext(t *testing.T) {
	f, c, details := batchDetailIntents(t, 3)
	ctx := context.Background()
	blocker, e := f.observer.Begin(ctx)
	if e != nil {
		t.Fatal(e)
	}
	defer blocker.Rollback(ctx)
	if _, e = blocker.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", OrdinaryLeaseBarrier); e != nil {
		t.Fatal(e)
	}
	bounded, cancel := context.WithTimeout(ctx, 100*time.Millisecond)
	defer cancel()
	added, e := c.EnqueueURLDetails(bounded, details)
	if e == nil || added != 0 {
		t.Fatal("barrier waiter published", added, e)
	}
	for _, d := range details {
		if f.client.redis.Exists(ctx, "scrape:"+d.ID).Val() != 0 {
			t.Fatal("cancelled barrier waiter changed queue")
		}
	}
}

func TestRealDetailPublicationBatchCost(t *testing.T) {
	f, c, details := batchDetailIntents(t, 500)
	ctx := context.Background()
	type measurement struct {
		Mode           string  `json:"mode"`
		Items          int     `json:"items"`
		ElapsedMS      float64 `json:"elapsed_ms"`
		AllocatedBytes uint64  `json:"allocated_bytes"`
	}
	samples := []measurement{}
	var expected map[string]any
	for _, mode := range []string{"original-single", "bounded-64"} {
		clearDetailPublications(t, f, details)
		runtime.GC()
		var before, after runtime.MemStats
		runtime.ReadMemStats(&before)
		start := time.Now()
		added := 0
		if mode == "original-single" {
			for _, d := range details {
				ok, e := c.EnqueueURLDetail(ctx, d)
				if e != nil {
					t.Fatal(e)
				}
				if ok {
					added++
				}
			}
		} else {
			for i := 0; i < len(details); i += URLDetailPublicationBatch {
				n, e := c.EnqueueURLDetails(ctx, details[i:min(i+URLDetailPublicationBatch, len(details))])
				if e != nil {
					t.Fatal(e)
				}
				added += n
			}
		}
		elapsed := time.Since(start)
		runtime.ReadMemStats(&after)
		if added != len(details) {
			t.Fatal("publication accounting differs", added)
		}
		state := detailPublicationState(t, f, details)
		if expected == nil {
			expected = state
		} else if !reflect.DeepEqual(expected, state) {
			t.Fatal("measured optimization changed output")
		}
		samples = append(samples, measurement{mode, len(details), float64(elapsed) / float64(time.Millisecond), after.TotalAlloc - before.TotalAlloc})
	}
	raw, e := json.Marshal(samples)
	if e != nil {
		t.Fatal(e)
	}
	t.Log(fmt.Sprintf("publication_cost=%s", raw))
}

func TestRealDetailBatchColdRetirementPreservesPublishedDetails(t *testing.T) {
	for _, provider := range []string{"dom/direct", "dom/proxy", "api_sniffer/direct", "api_sniffer/proxy"} {
		for _, committed := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/committed=%v", provider, committed), func(t *testing.T) {
				p := firstProviderBatchFixture(t, provider)
				a, claim := firstRetirementClaim(t, p)
				ctx := context.Background()
				cycle, err := a.BeginGreenhouseCycle(ctx, claim)
				if err != nil {
					t.Fatal(err)
				}
				urls := []string{}
				for i := 0; i < 65; i++ {
					urls = append(urls, "https://example.com/jobs/"+ordinaryID(t))
				}
				result, err := cycle.WriteURLOnlyBatch(ctx, urls)
				if err != nil || len(result.Details) != len(urls) {
					t.Fatal("committed details absent", err)
				}
				for i := 0; i < len(result.Details); i += URLDetailPublicationBatch {
					if added, err := cycle.EnqueueURLDetails(ctx, result.Details[i:min(i+URLDetailPublicationBatch, len(result.Details))]); err != nil || added != min(URLDetailPublicationBatch, len(result.Details)-i) {
						t.Fatal("bounded publication failed", added, err)
					}
				}
				if committed {
					if _, err := cycle.FinishSuccess(ctx, GreenhouseInventorySummary{Discovered: len(urls)}); err != nil {
						t.Fatal(err)
					}
				}
				canonical, due := coldCanonicalSnapshot(t, p.f), firstRetirementDue(t, p)
				expected := detailPublicationState(t, p.f, result.Details)
				if _, err := applyFirstFixture(t, p, true); err != nil {
					t.Fatal("cold retirement failed", err)
				}
				assertFirstRetirementSchedule(t, p, due)
				if coldCanonicalSnapshot(t, p.f) != canonical || !reflect.DeepEqual(expected, detailPublicationState(t, p.f, result.Details)) {
					t.Fatal("cold retirement changed canonical details or publications")
				}
				if err := a.Heartbeat(ctx, claim); !errors.Is(err, ErrAuthorityLost) {
					t.Fatal("retired batch writer retained authority", err)
				}
				restartPublicationRedisWithoutSave(t, p.f.client)
				assertFirstRetirementSchedule(t, p, due)
				if !reflect.DeepEqual(expected, detailPublicationState(t, p.f, result.Details)) {
					t.Fatal("cold Redis restart lost published details")
				}
			})
		}
	}
}
