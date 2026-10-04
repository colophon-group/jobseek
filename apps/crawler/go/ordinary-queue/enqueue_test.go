package queue

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"
)

func TestRealURLDetailAtomicConfigFirstTimeAndRecurringPromotion(t *testing.T) {
	c := privateRedis(t)
	ctx := context.Background()
	d := URLOnlyDetail{ID: ordinaryID(t), BoardID: ordinaryID(t), URL: "https://fixture.invalid/job/1", Due: time.Now()}
	added, err := c.EnqueueURLDetail(ctx, d)
	if err != nil || !added {
		t.Fatal("initial detail enqueue", err)
	}
	config, err := c.redis.HGetAll(ctx, "scrape:"+d.ID).Result()
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{"domain": "fixture.invalid", "source_url": d.URL, "board_id": d.BoardID, "description_r2_hash": "", "scrape_step": "0"}
	if !reflect.DeepEqual(config, want) {
		t.Fatal("detail config differs from ordinary queue ABI")
	}
	if score, err := c.redis.ZScore(ctx, "ft_scrapes_simple:fixture.invalid", d.ID).Result(); err != nil || score != 0 {
		t.Fatal("new posting lost urgent first-time tier", err)
	}
	added, err = c.EnqueueURLDetail(ctx, d)
	if err != nil || added {
		t.Fatal("repeated detail enqueue duplicated schedule", err)
	}
	hash := int64(-987)
	d.DescriptionHash = &hash
	d.Browser = true
	added, err = c.EnqueueURLDetail(ctx, d)
	if err != nil || !added {
		t.Fatal("browser recurring detail enqueue", err)
	}
	if score, err := c.redis.ZScore(ctx, "scrapes_browser:fixture.invalid", d.ID).Result(); err != nil || score != seconds(d.Due) {
		t.Fatal("recurring detail lost canonical due", err)
	}
	if value, err := c.redis.HGet(ctx, "scrape:"+d.ID, "description_r2_hash").Result(); err != nil || value != "-987" {
		t.Fatal("signed existing description hash changed", err)
	}
	// Empty-content relisting promotes a recurring detail to the urgent tier,
	// retaining the original script's per-ZSET NX semantics.
	d.DescriptionHash = nil
	added, err = c.EnqueueURLDetail(ctx, d)
	if err != nil || !added {
		t.Fatal("detail first-time promotion", err)
	}
	if score, err := c.redis.ZScore(ctx, "ft_scrapes_browser:fixture.invalid", d.ID).Result(); err != nil || score != 0 {
		t.Fatal("promoted detail not urgent", err)
	}
}

func TestRealURLDetailB0GuardAndMalformedOwnerBeforeConfigMutation(t *testing.T) {
	for _, corrupt := range []bool{false, true} {
		t.Run(map[bool]string{false: "posting_guard", true: "corrupt_owner"}[corrupt], func(t *testing.T) {
			c := privateRedis(t)
			ctx := context.Background()
			d := URLOnlyDetail{ID: ordinaryID(t), BoardID: ordinaryID(t), URL: "https://fixture.invalid/job/1", Due: time.Now()}
			if corrupt {
				if err := c.redis.HSet(ctx, "lightpanda-b0:producer-owner", "schema", "invalid").Err(); err != nil {
					t.Fatal(err)
				}
			} else {
				// A guard without a producer manifest is a corrupt boundary and
				// must fail before config or scheduling effects.
				if err := c.redis.HSet(ctx, "lightpanda-b0:legacy-guard", d.ID, "1").Err(); err != nil {
					t.Fatal(err)
				}
			}
			added, err := c.EnqueueURLDetail(ctx, d)
			if added || !errors.Is(err, ErrObservation) {
				t.Fatal("B0 boundary bypassed", err)
			}
			if n, err := c.redis.Exists(ctx, "scrape:"+d.ID, "ft_scrapes_simple:fixture.invalid", "scrapes_simple:fixture.invalid").Result(); err != nil || n != 0 {
				t.Fatal("refused enqueue mutated ordinary state", err)
			}
		})
	}
}
