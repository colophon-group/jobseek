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

func TestRealFirstPhenomMonitorColdRetirement(t *testing.T) {
	for _, provider := range []string{"phenom"} {
		for _, mode := range []string{"interrupted", "committed-before-ack", "changed-token"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				ctx := context.Background()
				p := firstOwnershipFixture(t)
				f := p.f
				metadata := `{"sitemap_url":"https://example.com/fixture.xml","scraper_type":"json-ld"}`
				boardURL := "https://example.com/careers"
				if _, err := f.observer.Exec(ctx, "UPDATE job_board SET board_url=$2,crawler_type=$4,throttle_key=$4,metadata=$3::jsonb WHERE id=$1::uuid", f.task.ID, boardURL, metadata, provider); err != nil {
					t.Fatal(err)
				}
				config := profileConfig()
				config["board_url"], config["crawler_type"], config["metadata"] = boardURL, provider, metadata
				config["company_id"], config["board_slug"] = f.company, "ordinary-"+f.company
				config["domain"], config["throttle_key"] = provider, provider
				if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, config).Err(); err != nil {
					t.Fatal(err)
				}
				if err := f.client.redis.Del(ctx, "monitors_simple:greenhouse", "ft_monitors_simple:greenhouse").Err(); err != nil {
					t.Fatal(err)
				}
				if err := f.client.redis.ZRem(ctx, "ready:simple:1", "greenhouse").Err(); err != nil {
					t.Fatal(err)
				}
				if err := f.client.redis.ZAdd(ctx, "monitors_simple:"+provider, redis.Z{Score: 1, Member: f.task.ID}).Err(); err != nil {
					t.Fatal(err)
				}
				if err := f.client.redis.ZAdd(ctx, "ready:simple:1", redis.Z{Score: 1, Member: provider}).Err(); err != nil {
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
				p.f.task = &Task{Worker: Simple, Kind: Monitor, ID: f.task.ID, Domain: provider}
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
					if _, err := f.observer.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb WHERE id=$1::uuid", f.task.ID, changed); err != nil {
						t.Fatal(err)
					}
					if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "metadata", changed).Err(); err != nil {
						t.Fatal(err)
					}
					before := snapshot(t, f.client)
					if _, err := applyFirstFixture(t, p, true); !errors.Is(err, ErrAuthorityLost) {
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
				if err := a.Heartbeat(ctx, claim); !errors.Is(err, ErrAuthorityLost) {
					t.Fatal("retired writer retained authority", err)
				}
				restartPublicationRedisWithoutSave(t, p.f.client)
				assertFirstRetirementSchedule(t, p, due)
			})
		}
	}
}
