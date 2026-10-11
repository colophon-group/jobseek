package main

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func capacityRedisFixture(t *testing.T, ctx context.Context) *redis.Client {
	t.Helper()
	if _, e := exec.LookPath("redis-server"); e != nil {
		t.Fatal("capacity contracts require real Redis")
	}
	return reaperRedisFixture(t, ctx)
}
func TestCapacityBoundsAndOriginalReachabilityIdentity(t *testing.T) {
	for _, args := range [][]string{{}, {"rebuild"}, {"summary", "extra"}, {"prune", "--max-scanned=0"}, {"prune", "--max-scanned=250001"}, {"prune", "--max-delete=100001"}, {"prune", "--cursor=-1"}, {"summary", "--format=html"}, {"inspect", "--unknown"}} {
		if _, e := parseCapacityOptions(args); e == nil {
			t.Fatal("accepted invalid bounded operation", args)
		}
	}
	for _, action := range []string{"summary", "inspect", "prune"} {
		o, e := parseCapacityOptions([]string{action})
		if e != nil || o.Apply || o.MaxScanned != 50000 || o.MaxDelete != 50000 {
			t.Fatal("default is not original dry run", o, e)
		}
	}
	b, e := os.ReadFile("../../src/lua/prune_orphan_scrape.lua")
	if e != nil || string(b) != capacityPruneLua {
		t.Fatal("original atomic reachability drift")
	}
	var policies []capacityPolicy
	if json.Unmarshal(capacityPolicyJSON, &policies) != nil || len(policies) != 15 {
		t.Fatal("capacity policy unavailable")
	}
	seen := map[string]bool{}
	var bytes int64
	for _, p := range policies {
		if seen[p.Name] || p.Name == "" {
			t.Fatal("duplicate policy")
		}
		seen[p.Name] = true
		bytes += p.BudgetBytes
	}
	if bytes > 3*1024*1024*1024*3/4 {
		t.Fatal("budget exceeded original headroom")
	}
}
func TestCapacityPruneRetainsEveryOwnershipRootAndCapsDeletion(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	c := capacityRedisFixture(t, ctx)
	for _, mode := range []string{"ft_scrapes_simple:", "scrapes_simple:", "ft_scrapes_browser:", "scrapes_browser:", "inflight:simple", "inflight:browser", "deadletter:simple", "deadletter:browser", "guard"} {
		id := strings.ReplaceAll(mode, ":", "-")
		if e := c.HSet(ctx, "scrape:"+id, "domain", "example.test").Err(); e != nil {
			t.Fatal(e)
		}
		if mode == "guard" {
			if e := c.HSet(ctx, "lightpanda-b0:legacy-guard", id, "1").Err(); e != nil {
				t.Fatal(e)
			}
			continue
		}
		key, member := mode+"example.test", id
		if strings.HasPrefix(mode, "inflight:") || strings.HasPrefix(mode, "deadletter:") {
			key = mode
			member = "scrape|example.test|" + id
		}
		if e := c.ZAdd(ctx, key, redis.Z{Score: 0, Member: member}).Err(); e != nil {
			t.Fatal(e)
		}
	}
	for _, id := range []string{"orphan1", "orphan2"} {
		if e := c.HSet(ctx, "scrape:"+id, "domain", "example.test").Err(); e != nil {
			t.Fatal(e)
		}
	}
	if e := c.HSet(ctx, "scrape:malformed", "source_url", "without-domain").Err(); e != nil {
		t.Fatal(e)
	}
	o := capacityOptions{MaxScanned: 100, MaxDelete: 1}
	before, e := capacityKeySnapshot(ctx, c)
	if e != nil {
		t.Fatal(e)
	}
	r, e := capacityPrune(ctx, c, o)
	if e != nil || !r.DryRun || r.Reachable != 9 || r.WouldDelete != 2 || r.MissingDomain != 1 || r.Deleted != 0 || r.NextCursor != 0 {
		t.Fatal("dry classification differs", r, e)
	}
	after, e := capacityKeySnapshot(ctx, c)
	if e != nil || !reflect.DeepEqual(before, after) {
		t.Fatal("dry run changed state", e)
	}
	o.Apply = true
	r, e = capacityPrune(ctx, c, o)
	if e != nil || r.Deleted != 1 || !r.BudgetExhausted || r.Reachable != 9 || r.MissingDomain != 1 {
		t.Fatal("bounded deletion differs", r, e)
	}
	if c.Exists(ctx, "scrape:orphan1", "scrape:orphan2").Val() != 1 || c.Exists(ctx, "scrape:malformed").Val() != 1 {
		t.Fatal("prune lost malformed config or exceeded budget")
	}
	for _, mode := range []string{"ft_scrapes_simple:", "scrapes_simple:", "ft_scrapes_browser:", "scrapes_browser:", "inflight:simple", "inflight:browser", "deadletter:simple", "deadletter:browser", "guard"} {
		if c.Exists(ctx, "scrape:"+strings.ReplaceAll(mode, ":", "-")).Val() != 1 {
			t.Fatal("ownership root pruned", mode)
		}
	}
}
func TestCapacityInspectionAndConservativeSummaryPreserveMachineContract(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	c := capacityRedisFixture(t, ctx)
	for _, id := range []string{"queued", "guarded", "orphan"} {
		if e := c.HSet(ctx, "scrape:"+id, "domain", "example.test").Err(); e != nil {
			t.Fatal(e)
		}
	}
	if e := c.HSet(ctx, "scrape:malformed", "source_url", "without-domain").Err(); e != nil {
		t.Fatal(e)
	}
	if e := c.ZAdd(ctx, "scrapes_simple:example.test", redis.Z{Score: 1, Member: "queued"}).Err(); e != nil {
		t.Fatal(e)
	}
	if e := c.HSet(ctx, "lightpanda-b0:legacy-guard", "guarded", "1").Err(); e != nil {
		t.Fatal(e)
	}
	if e := c.HSet(ctx, "inflight_strikes:simple", "a", "1", "b", "2").Err(); e != nil {
		t.Fatal(e)
	}
	if e := c.SAdd(ctx, "provider_fail_hosts:workday", "a", "b").Err(); e != nil {
		t.Fatal(e)
	}
	if e := c.Set(ctx, "provider_open:workday", "1", time.Minute).Err(); e != nil {
		t.Fatal(e)
	}
	clock := func() time.Time { return time.Unix(1000, 0) }
	before, e := capacityKeySnapshot(ctx, c)
	if e != nil {
		t.Fatal(e)
	}
	s, e := capacityInventory(ctx, c, false, clock)
	if e != nil || s.Precision != "conservative" || s.Timestamp != 1000 || s.Duration != 0 || s.Guarded != 1 || s.DBKeys != int64(len(before)) || !reflect.DeepEqual(s.ScrapeState, map[string]int64{"reachable_upper_bound": 2, "orphan_lower_bound": 2}) {
		t.Fatal("summary differs", s, e)
	}
	for _, row := range s.Families {
		if row.Name == "scrape_config" && (row.Keys != 4 || row.Items != 4 || row.SampleSize != 4 || row.SamplePersistent != 4 || row.EstimatedBytes <= 0) {
			t.Fatal("memory sample differs", row)
		}
		if row.Name == "provider_circuit" && (row.Keys != 2 || row.Items != 3 || row.SamplePersistent != 1) {
			t.Fatal("provider cardinality or TTL differs", row)
		}
	}
	s, e = capacityInventory(ctx, c, true, clock)
	if e != nil || s.Precision != "exact" || !reflect.DeepEqual(s.ScrapeState, map[string]int64{"reachable": 2, "orphan": 2}) {
		t.Fatal("exact reachability differs", s, e)
	}
	if !strings.Contains(capacityPrometheus(s), `jobseek_redis_scrape_config_state_keys{state="orphan"} 2`) || !strings.Contains(capacityPrometheus(s), `jobseek_redis_key_family_budget_bytes{family="scrape_config"} 1610612736`) {
		t.Fatal("metrics changed")
	}
	after, e := capacityKeySnapshot(ctx, c)
	if e != nil || !reflect.DeepEqual(before, after) {
		t.Fatal("inspection changed queue state", e)
	}
}

func capacityKeySnapshot(ctx context.Context, c *redis.Client) (map[string]string, error) {
	keys, err := c.Keys(ctx, "*").Result()
	if err != nil {
		return nil, err
	}
	result := map[string]string{}
	for _, key := range keys {
		value, err := c.Dump(ctx, key).Result()
		if err != nil {
			return nil, err
		}
		result[key] = value
	}
	return result, nil
}
func TestCapacityRejectsCorruptGuardBeforeDeletion(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	c := capacityRedisFixture(t, ctx)
	if e := c.Set(ctx, "lightpanda-b0:legacy-guard", "corrupt", 0).Err(); e != nil {
		t.Fatal(e)
	}
	if e := c.HSet(ctx, "scrape:orphan", "domain", "example.test").Err(); e != nil {
		t.Fatal(e)
	}
	if _, e := capacityPrune(ctx, c, capacityOptions{MaxScanned: 100, MaxDelete: 100, Apply: true}); e == nil {
		t.Fatal("corrupt guard allowed mutation")
	}
	if c.Exists(ctx, "scrape:orphan").Val() != 1 {
		t.Fatal("corrupt observation deleted config")
	}
}
