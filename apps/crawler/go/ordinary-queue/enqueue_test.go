package queue

import (
	"context"
	"errors"
	"reflect"
	"strconv"
	"testing"
	"time"
)

func TestRealURLDetailB0CohortsKeepOwnerExclusionAndOutsideSchedules(t *testing.T) {
	for _, cohort := range []string{"c1", "c2", "c3", "c4", "cdom"} {
		for _, browser := range []bool{false, true} {
			t.Run(cohort+"/browser="+strconv.FormatBool(browser), func(t *testing.T) {
				c := privateRedis(t)
				ctx := context.Background()
				owner := map[string]any{"schema": "jobseek.lightpanda.producer-owner/v1", "namespace": "production-b0", "shard_id": "lightpanda-b0", "routing_epoch": "245", "engine_owner": "go", "cohort": cohort, "board_count": "3", "board_slug:browser-use-careers": "1", "board_slug:bunq-careers": "1", "board_slug:algorized-careers": "1"}
				if err := c.redis.HSet(ctx, "lightpanda-b0:producer-owner", owner).Err(); err != nil {
					t.Fatal(err)
				}
				if err := c.redis.HSet(ctx, "lightpanda-b0:{production-b0}:route", "shard_id", "lightpanda-b0", "routing_epoch", "245", "engine_owner", "go", "claim_sequence", "0").Err(); err != nil {
					t.Fatal(err)
				}
				covered := URLOnlyDetail{ID: ordinaryID(t), BoardID: ordinaryID(t), URL: "https://fixture.invalid/job/covered", Due: time.Now(), Browser: browser}
				outside := URLOnlyDetail{ID: ordinaryID(t), BoardID: ordinaryID(t), URL: "https://fixture.invalid/job/outside", Due: time.Now(), Browser: browser}
				if err := c.redis.HSet(ctx, "board:"+covered.BoardID, "board_slug", "browser-use-careers").Err(); err != nil {
					t.Fatal(err)
				}
				if err := c.redis.HSet(ctx, "board:"+outside.BoardID, "board_slug", "outside-careers").Err(); err != nil {
					t.Fatal(err)
				}
				if added, err := c.EnqueueURLDetail(ctx, covered); added || !errors.Is(err, ErrObservation) {
					t.Fatal("B0-covered board entered ordinary detail queue", err)
				}
				if n, err := c.redis.Exists(ctx, "scrape:"+covered.ID).Result(); err != nil || n != 0 {
					t.Fatal("covered board config mutated", err)
				}
				if added, err := c.EnqueueURLDetail(ctx, outside); err != nil || !added {
					t.Fatal("valid outside detail rejected by active cohort", err)
				}
				worker := "simple"
				if browser {
					worker = "browser"
				}
				if score, err := c.redis.ZScore(ctx, "ft_scrapes_"+worker+":fixture.invalid", outside.ID).Result(); err != nil || score != 0 {
					t.Fatal("outside detail lost first-time schedule", err)
				}
				guarded := outside
				guarded.ID = ordinaryID(t)
				if err := c.redis.HSet(ctx, "lightpanda-b0:legacy-guard", guarded.ID, "1").Err(); err != nil {
					t.Fatal(err)
				}
				if added, err := c.EnqueueURLDetail(ctx, guarded); err != nil || added {
					t.Fatal("posting guard bypassed", err)
				}
				if n, err := c.redis.Exists(ctx, "scrape:"+guarded.ID).Result(); err != nil || n != 0 {
					t.Fatal("guarded posting config mutated", err)
				}
				if err := c.redis.HSet(ctx, "lightpanda-b0:{production-b0}:route", "routing_epoch", "246").Err(); err != nil {
					t.Fatal(err)
				}
				mismatch := outside
				mismatch.ID = ordinaryID(t)
				if added, err := c.EnqueueURLDetail(ctx, mismatch); added || !errors.Is(err, ErrObservation) {
					t.Fatal("mismatched route admitted", err)
				}
				if n, err := c.redis.Exists(ctx, "scrape:"+mismatch.ID).Result(); err != nil || n != 0 {
					t.Fatal("route refusal changed config", err)
				}
			})
		}
	}
}

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
