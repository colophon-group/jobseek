package queue

import (
	"context"
	"errors"
	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestRealFirstGroupedRSSProviderColdRetirement(t *testing.T) {
	for _, preset := range []string{"governmentjobs", "zoho_recruit", "hr_manager", "dom_rows", "dom_proxy_rows", "dom_rendered_rows", "generic_policy", "successfactors_policy", "teamtailor_policy", "dom_policy_rows", "dom_policy_urls"} {
		provider := "rss"
		if strings.HasPrefix(preset, "dom_") {
			provider = "dom"
		}
		worker := Simple
		if preset == "dom_rendered_rows" {
			worker = Browser
		}
		for _, mode := range []string{"interrupted", "committed-before-ack", "changed-token"} {
			t.Run(preset+"/"+mode, func(t *testing.T) {
				ctx := context.Background()
				p := firstOwnershipFixture(t)
				f := p.f
				metadata := `{"preset":"governmentjobs","agency":"fixture","scraper_type":"skip"}`
				boardURL := "https://www.governmentjobs.com/careers/fixture"
				if preset == "zoho_recruit" {
					boardURL = "https://fixture.zohorecruit.eu/jobs/Careers"
					metadata = `{"preset":"zoho_recruit","tenant":"fixture.eu","feed_url":"https://fixture.zohorecruit.eu/jobs/Careers/rss","scraper_type":"skip"}`
				}
				if preset == "hr_manager" {
					boardURL = "https://candidate.hr-manager.net/vacancies/list.aspx?customer=fixture"
					metadata = `{"preset":"hr_manager","customer":"fixture","scraper_type":"skip"}`
				}
				if strings.HasPrefix(preset, "dom_") {
					boardURL = "https://example.com/fixture"
					metadata = `{"rich_rows":{"row_selector":"article","link_selector":"a","description_selector":".description"},"scraper_type":"skip"}`
				}
				policy := strings.HasSuffix(preset, "_policy") || strings.HasPrefix(preset, "dom_policy_")
				if policy {
					boardURL = "https://example.com/fixture"
					prefix := `"preset":"generic","feed_url":"https://example.com/feed"`
					if preset == "successfactors_policy" {
						prefix = `"preset":"successfactors","feed_url":"https://example.com/googlefeed.xml"`
					}
					if preset == "teamtailor_policy" {
						prefix = `"preset":"teamtailor","feed_url":"https://example.com/jobs.rss"`
					}
					if preset == "dom_policy_rows" {
						prefix = `"rich_rows":{"row_selector":"article","link_selector":"a"}`
					}
					if preset == "dom_policy_urls" {
						prefix = `"link_selector":"a.job"`
					}
					metadata = `{` + prefix + `,"scraper_type":"skip","url_allowlist":"https://example\\.com/jobs/[0-9]+","job_filter":{"field":"title","include":"Engineer","exclude":"Intern","require_classification":true}}`
				}
				if preset == "dom_proxy_rows" {
					metadata = strings.Replace(metadata, `{"rich_rows":`, `{"proxy":true,"rich_rows":`, 1)
				}
				if worker == Browser {
					metadata = strings.Replace(metadata, `{"rich_rows":`, `{"render":true,"rich_rows":`, 1)
				}
				if _, err := f.observer.Exec(ctx, "UPDATE job_board SET board_url=$2,crawler_type=$4,throttle_key=$4,metadata=$3::jsonb,monitor_needs_browser=$5 WHERE id=$1::uuid", f.task.ID, boardURL, metadata, provider, worker == Browser); err != nil {
					t.Fatal(err)
				}
				config := profileConfig()
				config["board_url"], config["crawler_type"], config["metadata"] = boardURL, provider, metadata
				config["company_id"], config["board_slug"] = f.company, "ordinary-"+f.company
				config["domain"], config["throttle_key"] = provider, provider
				if worker == Browser {
					config["monitor_needs_browser"] = "1"
				}
				if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, config).Err(); err != nil {
					t.Fatal(err)
				}
				if err := f.client.redis.Del(ctx, "monitors_simple:greenhouse", "ft_monitors_simple:greenhouse").Err(); err != nil {
					t.Fatal(err)
				}
				if err := f.client.redis.ZRem(ctx, "ready:simple:1", "greenhouse").Err(); err != nil {
					t.Fatal(err)
				}
				if err := f.client.redis.ZAdd(ctx, "monitors_"+string(worker)+":"+provider, redis.Z{Score: 1, Member: f.task.ID}).Err(); err != nil {
					t.Fatal(err)
				}
				if err := f.client.redis.ZAdd(ctx, "ready:"+string(worker)+":1", redis.Z{Score: 1, Member: provider}).Err(); err != nil {
					t.Fatal(err)
				}
				plan, err := f.authority.StageOwnership(ctx, strings.Repeat("a", 40), []string{f.task.ID}, nil)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					_, _ = f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
				})
				p.plan = plan
				p.f.task = &Task{Worker: worker, Kind: Monitor, ID: f.task.ID, Domain: provider}
				a, claim := firstRetirementClaim(t, p)
				if mode == "committed-before-ack" {
					due := time.Now().UTC().Add(time.Hour).Truncate(time.Microsecond)
					if _, err := a.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
						_, err := tx.Exec(ctx, "UPDATE job_board SET next_check_at=$2 WHERE id=$1::uuid", f.task.ID, due)
						return err
					}); err != nil {
						t.Fatal(err)
					}
				}
				if mode == "changed-token" {
					changed := strings.ReplaceAll(metadata, "fixture", "changed")
					if policy {
						changed = strings.ReplaceAll(metadata, "Engineer", "Designer")
					}
					if strings.HasPrefix(preset, "dom_") && !policy {
						changed = strings.ReplaceAll(metadata, "article", "section")
					}
					if _, err := f.observer.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb WHERE id=$1::uuid", f.task.ID, changed); err != nil {
						t.Fatal(err)
					}
					if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "metadata", changed).Err(); err != nil {
						t.Fatal(err)
					}
					before := snapshot(t, f.client)
					if _, err := applyFirstFixture(t, p, true); !errors.Is(err, ErrAuthorityLost) && !errors.Is(err, ErrUnsupportedProfile) {
						t.Fatal("changed inventory admitted reversal", err)
					}
					if !reflect.DeepEqual(before, snapshot(t, f.client)) {
						t.Fatal("failed retirement mutated queues")
					}
					return
				}
				canonical, due := coldCanonicalSnapshot(t, p.f), firstRetirementDue(t, p)
				if result, err := applyFirstFixture(t, p, true); err != nil || result.State != "retired" {
					t.Fatal(result, err)
				}
				assertFirstRetirementSchedule(t, p, due)
				if coldCanonicalSnapshot(t, p.f) != canonical {
					t.Fatal("retirement changed canonical state")
				}
				if err := a.Heartbeat(ctx, claim); !errors.Is(err, ErrAuthorityLost) && !errors.Is(err, ErrUnsupportedProfile) {
					t.Fatal("retired writer retained authority", err)
				}
				restartPublicationRedisWithoutSave(t, p.f.client)
				assertFirstRetirementSchedule(t, p, due)
			})
		}
	}
}
