package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"testing"

	"github.com/redis/go-redis/v9"
)

// The fixture owns a separate Redis process. HGETALL counts include actual
// ownership/cache revalidation, so this catches per-posting fence round trips
// in the running worker rather than merely testing the batch API in isolation.
func urlPublicationHashReads(t *testing.T, client *redis.Client) int64 {
	t.Helper()
	text, err := client.Info(context.Background(), "commandstats").Result()
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range strings.Split(text, "\n") {
		if !strings.HasPrefix(line, "cmdstat_hgetall:") {
			continue
		}
		for _, field := range strings.Split(strings.TrimSpace(strings.TrimPrefix(line, "cmdstat_hgetall:")), ",") {
			if strings.HasPrefix(field, "calls=") {
				n, err := strconv.ParseInt(strings.TrimPrefix(field, "calls="), 10, 64)
				if err != nil {
					t.Fatal(err)
				}
				return n
			}
		}
	}
	return 0
}

func TestRealURLMonitorPublishesLargeInventoriesWithinFenceReadBudget(t *testing.T) {
	const count = 513 // multiple provider pages, SQL chunks and publication batches
	for _, provider := range []string{"workday", "smartrecruiters"} {
		for _, repair := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/repair=%v", provider, repair), func(t *testing.T) {
				metadata := `{"scraper_type":"` + provider + `"}`
				if provider == "workday" {
					metadata = `{"all_sites":false,"scraper_type":"workday"}`
				}
				f := privateRichPipelineFixture(t, provider, metadata)
				ctx := context.Background()
				urls := make([]string, count)
				host := "jobs.smartrecruiters.com"
				for i := range urls {
					if provider == "workday" {
						host = "fixture.wd1.myworkdayjobs.com"
						urls[i] = fmt.Sprintf("https://%s/Careers/job/Remote/Engineer_R%04d", host, i)
					} else {
						urls[i] = fmt.Sprintf("https://%s/fixture/A%04d", host, i)
					}
				}
				if repair {
					_, err := f.pg.Exec(ctx, `INSERT INTO job_posting(company_id,board_id,source_url,first_seen_at,last_seen_at,next_scrape_at,is_active,titles,locales)
SELECT $1::uuid,$2::uuid,u.url,now()-interval '1 day',now()-interval '1 day',now()-interval '1 day',true,'{}','{}' FROM unnest($3::text[]) AS u(url)`, f.company, f.board, urls)
					if err != nil {
						t.Fatal(err)
					}
				}
				claim, circuits := claimFixture(t, f)
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					offset, limit := 0, 100
					if provider == "workday" {
						var body struct{ Offset, Limit int }
						if json.NewDecoder(r.Body).Decode(&body) != nil {
							t.Error("invalid Workday page request")
							w.WriteHeader(400)
							return
						}
						offset, limit = body.Offset, body.Limit
						if r.Method != "POST" || r.URL.Path != "/wday/cxs/fixture/Careers/jobs" {
							t.Error("monitor fetched outside inventory")
						}
					} else {
						if r.Method != "GET" || r.URL.Path != "/v1/companies/fixture/postings" {
							t.Error("monitor fetched outside inventory")
						}
						if r.URL.Query().Get("offset") != "" {
							offset, _ = strconv.Atoi(r.URL.Query().Get("offset"))
						}
					}
					if offset < 0 || offset >= count || limit < 1 || limit > 100 {
						t.Error("unbounded page request")
						w.WriteHeader(400)
						return
					}
					rows := []map[string]string{}
					for i := offset; i < min(offset+limit, count); i++ {
						if provider == "workday" {
							rows = append(rows, map[string]string{"externalPath": fmt.Sprintf("/job/Remote/Engineer_R%04d", i)})
						} else {
							rows = append(rows, map[string]string{"id": fmt.Sprintf("A%04d", i)})
						}
					}
					w.Header().Set("Content-Type", "application/json")
					if provider == "workday" {
						json.NewEncoder(w).Encode(map[string]any{"total": count, "jobPostings": rows})
					} else {
						json.NewEncoder(w).Encode(map[string]any{"totalFound": count, "content": rows})
					}
				}))
				before := urlPublicationHashReads(t, f.r)
				preparer := &pipelinePreparer{}
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits)
				if err != nil || result == nil || !result.Settled || preparer.at != 0 {
					t.Fatalf("URL monitor did not complete separate-detail publication: %v", err)
				}
				reads := urlPublicationHashReads(t, f.r) - before
				t.Logf("posted=%d ownership/cache hash reads=%d", count, reads)
				if reads > count/4+64 {
					t.Fatalf("large inventory exceeded ownership/cache observation budget: %d", reads)
				}
				if !repair && result.Batches.Inserted != count || repair && result.Batches.Touched != count {
					t.Fatal("committed inventory accounting changed")
				}
				if n, err := f.r.ZCard(ctx, "ft_scrapes_simple:"+host).Result(); err != nil || n != count {
					t.Fatal("some committed first-time details are not queued", err)
				}
				var stubs int
				if err := f.pg.QueryRow(ctx, `SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND source_url=ANY($2::text[]) AND is_active AND next_scrape_at IS NOT NULL AND description_r2_hash IS NULL AND cardinality(titles)=0 AND cardinality(locales)=0`, f.board, urls).Scan(&stubs); err != nil || stubs != count {
					t.Fatal("URL monitor changed detail content or scheduling", err)
				}
				rows, err := f.pg.Query(ctx, "SELECT id::text,source_url FROM job_posting WHERE board_id=$1::uuid AND source_url=ANY($2::text[])", f.board, urls)
				if err != nil {
					t.Fatal(err)
				}
				defer rows.Close()
				for rows.Next() {
					var id, source string
					if rows.Scan(&id, &source) != nil {
						t.Fatal("posting identity unavailable")
					}
					if f.r.HGet(ctx, "scrape:"+id, "board_id").Val() != f.board || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
						t.Fatal("published cache lost its canonical board/URL")
					}
				}
				if rows.Err() != nil {
					t.Fatal(rows.Err())
				}
				assertRichDeadlineAndLease(t, f, provider)
			})
		}
	}
}
