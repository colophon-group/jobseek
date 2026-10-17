package queue

import (
	"context"
	"maps"
	"reflect"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func coldProjectionRouteCases() []struct {
	name, board, source string
	config              map[string]string
} {
	proxy := workdayDetailConfig()
	proxy["metadata"] = `{"scraper_type":"workday","scraper_config":{"proxy":true}}`
	jsonldProxy := jsonldDetailConfig()
	jsonldProxy["metadata"] = `{"scraper_type":"json-ld","scraper_config":{"proxy":true}}`
	dom := jsonldDetailConfig()
	dom["metadata"] = `{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}]}}`
	return []struct {
		name, board, source string
		config              map[string]string
	}{
		{"workday", profileBoardID, "https://fixture.wd1.myworkdayjobs.com/Careers/job/City/Engineer_JR001", workdayDetailConfig()},
		{"workday-proxy", profileBoardID, "https://fixture.wd1.myworkdayjobs.com/Careers/job/City/Engineer_JR001", proxy},
		{"smartrecruiters", jsonldBoardID, apiDetailCases[0].source, apiDetailConfig("smartrecruiters")},
		{"jsonld", jsonldBoardID, "https://example.com/job/1", jsonldDetailConfig()},
		{"jsonld-proxy", jsonldBoardID, "https://example.com/job/1", jsonldProxy},
		{"dom", jsonldBoardID, "https://example.com/job/1", dom},
		{"workable-fallback", jsonldBoardID, apiDetailCases[1].source, apiDetailConfig("workable")},
	}
}

func TestRealColdProjectionSelectedProfilesRestoreMissingSchedules(t *testing.T) {
	for _, provider := range []string{"workday", "workday/proxy", "smartrecruiters", "jsonld", "jsonld/proxy", "dom", "dom/proxy", "embedded"} {
		t.Run(provider, func(t *testing.T) {
			var p firstOwnerFixture
			switch provider {
			case "workday", "workday/proxy", "embedded":
				p = firstAPIDetailFixture(t, provider)
			case "smartrecruiters":
				p = firstIndependentDetailFixture(t, `{"scraper_type":"smartrecruiters"}`, apiDetailCases[0].source, apiDetailCases[0].domain)
			case "jsonld", "jsonld/proxy":
				metadata := `{"scraper_type":"json-ld"}`
				if provider == "jsonld/proxy" {
					metadata = `{"scraper_type":"json-ld","scraper_config":{"proxy":true}}`
				}
				p = firstIndependentDetailFixture(t, metadata, "https://example.com/job/1", "example.com")
			default:
				metadata := `{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}]}}`
				if provider == "dom/proxy" {
					metadata = `{"scraper_type":"dom","scraper_config":{"proxy":true,"steps":[{"tag":"h1","field":"title"}]}}`
				}
				p = firstIndependentDetailFixture(t, metadata, "https://example.com/job/1", "example.com")
			}
			ctx, client, task := context.Background(), p.f.client.redis, p.f.task
			var due time.Time
			var hash *int64
			var source string
			if err := p.f.observer.QueryRow(ctx, "SELECT next_scrape_at,description_r2_hash,source_url FROM job_posting WHERE id=$1::uuid", task.ID).Scan(&due, &hash, &source); err != nil {
				t.Fatal(err)
			}
			firstQueue := "ft_scrapes_simple:" + task.Domain
			recurringQueue := "scrapes_simple:" + task.Domain
			queue, wanted := firstQueue, float64(0)
			if hash != nil {
				queue, wanted = recurringQueue, seconds(due)
			}
			if err := client.Del(ctx, "scrape:"+task.ID).Err(); err != nil {
				t.Fatal(err)
			}
			if err := client.ZRem(ctx, firstQueue, task.ID).Err(); err != nil {
				t.Fatal(err)
			}
			if err := client.ZRem(ctx, recurringQueue, task.ID).Err(); err != nil {
				t.Fatal(err)
			}
			foreign := ordinaryID(t)
			if err := client.ZAdd(ctx, queue, redis.Z{Score: -17, Member: foreign}).Err(); err != nil {
				t.Fatal(err)
			}
			if _, err := applyFirstFixture(t, p, false); err != nil {
				t.Fatal("original cold activation failed", err)
			}
			if client.ZScore(ctx, queue, task.ID).Val() != wanted || client.ZScore(ctx, queue, task.ID).Err() != nil || client.ZScore(ctx, queue, foreign).Val() != -17 || client.HGet(ctx, "scrape:"+task.ID, "source_url").Val() != source {
				t.Fatal("cold repair lost the schedule, actual source, or foreign member")
			}
			var after time.Time
			var afterHash *int64
			if err := p.f.observer.QueryRow(ctx, "SELECT next_scrape_at,description_r2_hash FROM job_posting WHERE id=$1::uuid", task.ID).Scan(&after, &afterHash); err != nil || !after.Equal(due) || !reflect.DeepEqual(hash, afterHash) {
				t.Fatal("cold repair changed canonical content or deadline", err)
			}
			if _, err := applyFirstFixture(t, p, true); err != nil {
				t.Fatal("supported retirement failed after selected-profile repair", err)
			}
		})
	}
}

func TestColdProjectionSelectedProfileMatchesExistingRouteInspection(t *testing.T) {
	for _, tc := range coldProjectionRouteCases() {
		t.Run(tc.name, func(t *testing.T) {
			original, err := inspectDetail(tc.board, tc.config, tc.source, Simple)
			if err != nil {
				t.Fatal(err)
			}
			binding := ownershipDetail{BoardID: tc.board, Profile: original.Profile, Worker: Simple}
			for _, source := range []string{tc.source, "https://foreign.example/job/1", "https://u:p@example.com/job/1", tc.source + "?changed=1", "not a URL"} {
				before, oldErr := inspectDetail(tc.board, tc.config, source, Simple)
				after, newErr := inspectColdDetailProjectionRoute(binding, tc.config, source)
				if (oldErr == nil) != (newErr == nil) || oldErr == nil && !reflect.DeepEqual(before, after) {
					t.Fatal("selected inspector changed the actual route or full binding")
				}
			}
			changed := maps.Clone(tc.config)
			changed["metadata"] = `{"scraper_type":"json-ld"}`
			after, err := inspectColdDetailProjectionRoute(binding, changed, tc.source)
			if err == nil && after.Profile == binding.Profile && after.EffectiveBoardSHA256 == original.EffectiveBoardSHA256 {
				t.Fatal("changed full config retained the staged route binding")
			}
			binding.Worker = Browser
			if _, err := inspectColdDetailProjectionRoute(binding, tc.config, tc.source); err == nil {
				t.Fatal("selected inspector admitted a different queue lane")
			}
		})
	}
}

func BenchmarkColdProjectionSelectedRoute(b *testing.B) {
	for _, tc := range coldProjectionRouteCases()[:6] {
		original, err := inspectDetail(tc.board, tc.config, tc.source, Simple)
		if err != nil {
			b.Fatal(err)
		}
		binding := ownershipDetail{BoardID: tc.board, Profile: original.Profile, Worker: Simple}
		for _, selected := range []bool{false, true} {
			name := "all-scrapers"
			if selected {
				name = "selected-profile"
			}
			b.Run(tc.name+"/"+name, func(b *testing.B) {
				b.ReportAllocs()
				for b.Loop() {
					if selected {
						_, err = inspectColdDetailProjectionRoute(binding, tc.config, tc.source)
					} else {
						_, err = inspectDetail(tc.board, tc.config, tc.source, Simple)
					}
					if err != nil {
						b.Fatal(err)
					}
				}
			})
		}
	}
}
