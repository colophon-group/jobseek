package queue

import (
	"context"
	"reflect"
	"strconv"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestRealColdDetailProjectionRestoresWhollyMissingSchedules(t *testing.T) {
	for _, worker := range []WorkerType{Simple, Browser} {
		for _, hasDescription := range []bool{false, true} {
			t.Run(string(worker)+"/"+strconv.FormatBool(hasDescription), func(t *testing.T) {
				metadata := `{"scraper_type":"json-ld"}`
				if worker == Browser {
					metadata = `{"scraper_type":"json-ld","scraper_config":{"render":true}}`
				}
				p := projectionFixture(t, worker, metadata)
				ctx, r := context.Background(), p.f.client.redis
				id, key := p.f.task.ID, "scrape:"+p.f.task.ID
				due := time.Now().UTC().Add(-48 * time.Hour).Truncate(time.Microsecond)
				var hash *int64
				if hasDescription {
					value := int64(123)
					hash = &value
				}
				if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=$2,description_r2_hash=$3 WHERE id=$1::uuid", id, due, hash); err != nil {
					t.Fatal(err)
				}
				for _, lane := range []WorkerType{Simple, Browser} {
					r.ZRem(ctx, "ft_scrapes_"+string(lane)+":example.com", id)
					r.ZRem(ctx, "scrapes_"+string(lane)+":example.com", id)
				}
				r.Del(ctx, key)
				foreign := ordinaryID(t)
				r.ZAdd(ctx, "ft_scrapes_browser:example.com", redis.Z{Score: -17, Member: foreign})
				if _, err := applyFirstFixture(t, p, false); err != nil {
					t.Fatal("missing schedule adoption failed", err)
				}
				queue, want := "ft_scrapes_"+string(worker)+":example.com", float64(0)
				if hasDescription {
					queue, want = "scrapes_"+string(worker)+":example.com", seconds(due)
				}
				if score, err := r.ZScore(ctx, queue, id).Result(); err != nil || score != want {
					t.Fatal("canonical priority/deadline not restored", score, err)
				}
				if r.HGet(ctx, key, "board_id").Val() != p.plan.document.Details[0].BoardID || r.HGet(ctx, key, "source_url").Val() != "https://example.com/job/REQ100" || r.HGet(ctx, key, "scrape_step").Val() != "0" || r.ZScore(ctx, "ft_scrapes_browser:example.com", foreign).Val() != -17 {
					t.Fatal("canonical identity or unrelated queue changed")
				}
				var after time.Time
				var afterHash *int64
				if err := p.f.observer.QueryRow(ctx, "SELECT next_scrape_at,description_r2_hash FROM job_posting WHERE id=$1::uuid", id).Scan(&after, &afterHash); err != nil || !after.Equal(due) || !reflect.DeepEqual(hash, afterHash) {
					t.Fatal("reconstruction changed canonical content/deadline", err)
				}
				before := r.HGetAll(ctx, key).Val()
				if _, err := applyFirstFixture(t, p, false); err != nil || !reflect.DeepEqual(before, r.HGetAll(ctx, key).Val()) || r.ZScore(ctx, queue, id).Val() != want {
					t.Fatal("exact adoption retry changed restored schedule", err)
				}
			})
		}
	}
}

func TestRealColdMissingScheduleRecoveryPreservesOtherLifecycleState(t *testing.T) {
	for _, mode := range []string{"deadletter-simple", "deadletter-browser", "orphan-cache", "inactive", "unscheduled", "native-token"} {
		t.Run(mode, func(t *testing.T) {
			p := projectionFixture(t, Simple, `{"scraper_type":"json-ld"}`)
			ctx, r := context.Background(), p.f.client.redis
			id, key := p.f.task.ID, "scrape:"+p.f.task.ID
			r.ZRem(ctx, "ft_scrapes_simple:example.com", id)
			if mode != "orphan-cache" {
				r.Del(ctx, key)
			} else {
				r.HSet(ctx, key, "scrape_step", "1", "description_r2_hash", "-321")
			}
			switch mode {
			case "deadletter-simple":
				r.ZAdd(ctx, "deadletter:simple", redis.Z{Score: 42, Member: "scrape|example.com|" + id})
			case "deadletter-browser":
				r.ZAdd(ctx, "deadletter:browser", redis.Z{Score: 43, Member: "scrape|example.com|" + id})
			case "inactive":
				if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET is_active=false WHERE id=$1::uuid", id); err != nil {
					t.Fatal(err)
				}
			case "unscheduled":
				if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=NULL WHERE id=$1::uuid", id); err != nil {
					t.Fatal(err)
				}
			case "native-token":
				r.HSet(ctx, "inflight_tokens:browser", "scrape|example.com|"+id, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
			}
			before := r.HGetAll(ctx, key).Val()
			_, err := applyFirstFixture(t, p, false)
			if (err != nil) != (mode == "native-token") {
				t.Fatal("lifecycle disposition differs", err)
			}
			if !reflect.DeepEqual(before, r.HGetAll(ctx, key).Val()) {
				t.Fatal("retained lifecycle cache changed")
			}
			for _, lane := range []string{"simple", "browser"} {
				for _, prefix := range []string{"ft_scrapes_", "scrapes_"} {
					if r.ZScore(ctx, prefix+lane+":example.com", id).Err() != redis.Nil {
						t.Fatal("retained lifecycle state was requeued")
					}
				}
			}
			if mode == "native-token" && (firstFixtureState(t, p) != "staged" || r.Exists(ctx, ownershipProjectionKey).Val() != 0) {
				t.Fatal("native token refusal granted ownership")
			}
		})
	}
}
