package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
)

//go:embed redis_capacity_policy.json
var capacityPolicyJSON []byte

//go:embed redis_capacity_prune.lua
var capacityPruneLua string

type capacityOptions struct {
	Action, Format        string
	AfterID               string
	Limit                 int
	Apply                 bool
	Cursor                uint64
	MaxScanned, MaxDelete int
}

func parseCapacityOptions(args []string) (capacityOptions, error) {
	o := capacityOptions{Format: "json", MaxScanned: 50000, MaxDelete: 50000, Limit: 10000}
	if len(args) == 0 {
		return o, errors.New("capacity action required")
	}
	o.Action = args[0]
	if o.Action != "summary" && o.Action != "inspect" && o.Action != "prune" && o.Action != "rebuild" {
		return o, errors.New("unsupported capacity action")
	}
	f := flag.NewFlagSet("redis-capacity", flag.ContinueOnError)
	f.SetOutput(io.Discard)
	f.StringVar(&o.Format, "format", "json", "")
	f.BoolVar(&o.Apply, "apply", false, "")
	f.Uint64Var(&o.Cursor, "cursor", 0, "")
	f.IntVar(&o.MaxScanned, "max-scanned", 50000, "")
	f.IntVar(&o.MaxDelete, "max-delete", 50000, "")
	f.StringVar(&o.AfterID, "after-id", "", "")
	f.IntVar(&o.Limit, "limit", 10000, "")
	if f.Parse(args[1:]) != nil || f.NArg() != 0 || (o.Format != "json" && o.Format != "prometheus") || o.MaxScanned < 1 || o.MaxScanned > 250000 || o.MaxDelete < 1 || o.MaxDelete > 100000 {
		return o, errors.New("invalid capacity options")
	}
	if o.Limit < 1 || o.Limit > 50000 || o.AfterID != "" && !capacityUUID.MatchString(o.AfterID) {
		return o, errors.New("invalid rebuild bounds or UUID cursor")
	}
	return o, nil
}

type capacityPolicy struct {
	Name        string `json:"name"`
	Owner       string `json:"owner"`
	Lifecycle   string `json:"lifecycle"`
	TTL         string `json:"ttl"`
	BudgetKeys  int64  `json:"budget_keys"`
	BudgetItems int64  `json:"budget_items"`
	BudgetBytes int64  `json:"budget_bytes"`
}
type capacityFamily struct {
	capacityPolicy
	Keys               int64   `json:"keys"`
	Items              int64   `json:"items"`
	SampleSize         int     `json:"sample_size"`
	SampleAverageBytes float64 `json:"sample_average_bytes"`
	SamplePersistent   int     `json:"sample_persistent"`
	EstimatedBytes     int64   `json:"estimated_bytes"`
}
type capacitySnapshot struct {
	Timestamp   float64          `json:"snapshot_unixtime"`
	Duration    float64          `json:"duration_seconds"`
	DBKeys      int64            `json:"db_keys"`
	Redis       map[string]any   `json:"redis"`
	ScrapeState map[string]int64 `json:"scrape_config_state"`
	Precision   string           `json:"scrape_config_state_precision"`
	Guarded     int64            `json:"guarded_scrape_configs"`
	Families    []capacityFamily `json:"families"`
	QueueKeys   int              `json:"queue_keys_scanned"`
}

var capacityPrefixes = []struct{ Prefix, Family string }{
	{"scrape:", "scrape_config"}, {"board:", "board_config"},
	{"ft_scrapes_simple:", "scrape_queue_first"}, {"ft_scrapes_browser:", "scrape_queue_first"},
	{"scrapes_simple:", "scrape_queue_recurring"}, {"scrapes_browser:", "scrape_queue_recurring"},
	{"ft_monitors_simple:", "monitor_queue_first"}, {"ft_monitors_browser:", "monitor_queue_first"},
	{"monitors_simple:", "monitor_queue_recurring"}, {"monitors_browser:", "monitor_queue_recurring"},
	{"ready:", "ready_queue"}, {"inflight_strikes:", "inflight_strikes"}, {"inflight:", "inflight"},
	{"deadletter:", "deadletter"}, {"delay:", "delay"}, {"ratelimit:", "rate_limit"},
	{"host_fail:", "host_circuit"}, {"host_open:", "host_circuit"}, {"host_probe:", "host_circuit"},
	{"provider_fail_hosts:", "provider_circuit"}, {"provider_open:", "provider_circuit"}, {"provider_probe:", "provider_circuit"},
}

func capacityClassify(key string) string {
	for _, p := range capacityPrefixes {
		if strings.HasPrefix(key, p.Prefix) {
			return p.Family
		}
	}
	return "other"
}
func capacityQueue(f string) bool {
	switch f {
	case "scrape_queue_first", "scrape_queue_recurring", "monitor_queue_first", "monitor_queue_recurring", "ready_queue", "inflight", "deadletter":
		return true
	}
	return false
}

type capacityPruneReport struct {
	DryRun          bool   `json:"dry_run"`
	StartCursor     uint64 `json:"start_cursor"`
	NextCursor      uint64 `json:"next_cursor"`
	Scanned         int    `json:"scanned"`
	Deleted         int    `json:"deleted"`
	WouldDelete     int    `json:"would_delete"`
	Reachable       int    `json:"reachable"`
	Missing         int    `json:"missing"`
	MissingDomain   int    `json:"missing_domain"`
	BudgetExhausted bool   `json:"delete_budget_exhausted"`
}

func capacityPrune(ctx context.Context, c *redis.Client, o capacityOptions) (capacityPruneReport, error) {
	r := capacityPruneReport{DryRun: !o.Apply, StartCursor: o.Cursor, NextCursor: o.Cursor}
	if o.MaxScanned < 1 || o.MaxScanned > 250000 || o.MaxDelete < 1 || o.MaxDelete > 100000 {
		return r, errors.New("invalid capacity prune bounds")
	}
	sha, e := c.ScriptLoad(ctx, capacityPruneLua).Result()
	if e != nil {
		return r, errors.New("load capacity reachability script failed")
	}
	for r.Scanned < o.MaxScanned {
		keys, next, e := c.Scan(ctx, r.NextCursor, "scrape:*", int64(min(2000, o.MaxScanned-r.Scanned))).Result()
		if e != nil {
			return r, errors.New("capacity scan failed")
		}
		r.NextCursor = next
		keys = keys[:min(len(keys), o.MaxScanned-r.Scanned)]
		r.Scanned += len(keys)
		pipe := c.Pipeline()
		cmds := make([]*redis.Cmd, len(keys))
		for i, key := range keys {
			cmds[i] = pipe.EvalSha(ctx, sha, nil, key[7:], "0")
		}
		if _, e = pipe.Exec(ctx); e != nil {
			return r, errors.New("capacity classification failed")
		}
		orphans := []string{}
		for i, cmd := range cmds {
			n, e := cmd.Int()
			if e != nil {
				return r, errors.New("invalid capacity classification")
			}
			switch n {
			case 0:
				r.Reachable++
			case 1:
				orphans = append(orphans, keys[i][7:])
			case -1:
				r.Missing++
			case -2:
				r.MissingDomain++
			default:
				return r, errors.New("unknown capacity classification")
			}
		}
		r.WouldDelete += len(orphans)
		if o.Apply && r.Deleted < o.MaxDelete && len(orphans) > 0 {
			pipe = c.Pipeline()
			cmds = nil
			for _, id := range orphans[:min(len(orphans), o.MaxDelete-r.Deleted)] {
				cmds = append(cmds, pipe.EvalSha(ctx, sha, nil, id, "1"))
			}
			if _, e = pipe.Exec(ctx); e != nil {
				return r, errors.New("capacity prune acknowledgement failed")
			}
			for _, cmd := range cmds {
				n, e := cmd.Int()
				if e != nil {
					return r, errors.New("invalid capacity prune acknowledgement")
				}
				if n == 1 {
					r.Deleted++
				}
			}
		}
		if next == 0 {
			break
		}
	}
	r.BudgetExhausted = o.Apply && r.WouldDelete > r.Deleted
	return r, nil
}
func capacityInventory(ctx context.Context, c *redis.Client, exact bool, clock func() time.Time) (capacitySnapshot, error) {
	start := clock()
	s := capacitySnapshot{Timestamp: float64(start.UnixNano()) / 1e9, Families: []capacityFamily{}, ScrapeState: map[string]int64{}}
	counts, items := map[string]int64{}, map[string]int64{}
	samples, logical := map[string][]string{}, map[string][]string{}
	var cursor uint64
	for {
		keys, next, e := c.Scan(ctx, cursor, "", 2000).Result()
		if e != nil {
			return s, errors.New("capacity inventory scan failed")
		}
		for _, key := range keys {
			f := capacityClassify(key)
			counts[f]++
			if len(samples[f]) < 128 {
				samples[f] = append(samples[f], key)
			}
			if capacityQueue(f) || f == "inflight_strikes" || f == "provider_circuit" {
				logical[f] = append(logical[f], key)
			}
		}
		cursor = next
		if cursor == 0 {
			break
		}
	}
	for f, n := range counts {
		items[f] = n
	}
	for f, keys := range logical {
		var total int64
		for start := 0; start < len(keys); start += 500 {
			pipe := c.Pipeline()
			cmds := []*redis.IntCmd{}
			for _, key := range keys[start:min(start+500, len(keys))] {
				switch {
				case capacityQueue(f):
					cmds = append(cmds, pipe.ZCard(ctx, key))
				case f == "inflight_strikes":
					cmds = append(cmds, pipe.HLen(ctx, key))
				case strings.HasPrefix(key, "provider_fail_hosts:"):
					cmds = append(cmds, pipe.SCard(ctx, key))
				default:
					cmds = append(cmds, pipe.Exists(ctx, key))
				}
			}
			if _, e := pipe.Exec(ctx); e != nil {
				return s, errors.New("capacity logical count failed")
			}
			for _, cmd := range cmds {
				total += cmd.Val()
			}
		}
		items[f] = total
		if capacityQueue(f) {
			s.QueueKeys += len(keys)
		}
	}
	var policies []capacityPolicy
	if json.Unmarshal(capacityPolicyJSON, &policies) != nil {
		return s, errors.New("invalid capacity policies")
	}
	for _, policy := range policies {
		row := capacityFamily{capacityPolicy: policy, Keys: counts[policy.Name], Items: items[policy.Name]}
		keys := samples[policy.Name]
		pipe := c.Pipeline()
		mem, ttl := []*redis.IntCmd{}, []*redis.DurationCmd{}
		for _, key := range keys {
			mem = append(mem, pipe.MemoryUsage(ctx, key))
			ttl = append(ttl, pipe.PTTL(ctx, key))
		}
		if len(keys) > 0 {
			if _, e := pipe.Exec(ctx); e != nil && !errors.Is(e, redis.Nil) {
				return s, errors.New("capacity memory sample failed")
			}
		}
		var bytes int64
		for i, cmd := range mem {
			v, e := cmd.Result()
			if e == nil {
				bytes += v
				row.SampleSize++
			} else if !errors.Is(e, redis.Nil) {
				return s, errors.New("capacity sample unavailable")
			}
			if ttl[i].Err() != nil {
				return s, errors.New("capacity sample TTL unavailable")
			}
			if ttl[i].Val() == -time.Nanosecond {
				row.SamplePersistent++
			}
		}
		if row.SampleSize > 0 {
			average := float64(bytes) / float64(row.SampleSize)
			row.SampleAverageBytes = math.RoundToEven(average*100) / 100
			row.EstimatedBytes = int64(math.Ceil(average * float64(row.Keys)))
		}
		s.Families = append(s.Families, row)
	}
	var e error
	s.Guarded, e = c.HLen(ctx, "lightpanda-b0:legacy-guard").Result()
	if e != nil {
		return s, errors.New("capacity guard observation failed")
	}
	if exact {
		s.Precision = "exact"
		s.ScrapeState = map[string]int64{"reachable": 0, "orphan": 0}
		sha, e := c.ScriptLoad(ctx, capacityPruneLua).Result()
		if e != nil {
			return s, errors.New("capacity reachability load failed")
		}
		cursor = 0
		for {
			keys, next, e := c.Scan(ctx, cursor, "scrape:*", 2000).Result()
			if e != nil {
				return s, errors.New("capacity reachability scan failed")
			}
			pipe := c.Pipeline()
			cmds := []*redis.Cmd{}
			for _, key := range keys {
				cmds = append(cmds, pipe.EvalSha(ctx, sha, nil, key[7:], "0"))
			}
			if len(cmds) > 0 {
				if _, e = pipe.Exec(ctx); e != nil {
					return s, errors.New("capacity reachability failed")
				}
			}
			for _, cmd := range cmds {
				n, e := cmd.Int()
				if e != nil {
					return s, errors.New("invalid reachability result")
				}
				switch n {
				case 0:
					s.ScrapeState["reachable"]++
				case 1, -2:
					s.ScrapeState["orphan"]++
				case -1:
				default:
					return s, errors.New("unknown reachability result")
				}
			}
			cursor = next
			if cursor == 0 {
				break
			}
		}
	} else {
		s.Precision = "conservative"
		slots := items["scrape_queue_first"] + items["scrape_queue_recurring"] + items["inflight"] + items["deadletter"] + s.Guarded
		orphan := max(int64(0), counts["scrape_config"]-slots)
		s.ScrapeState = map[string]int64{"reachable_upper_bound": counts["scrape_config"] - orphan, "orphan_lower_bound": orphan}
	}
	info := map[string]string{}
	for _, section := range []string{"memory", "persistence", "stats"} {
		raw, e := c.Info(ctx, section).Result()
		if e != nil {
			return s, errors.New("capacity Redis information unavailable")
		}
		for _, line := range strings.Split(raw, "\n") {
			k, v, ok := strings.Cut(strings.TrimSpace(line), ":")
			if ok {
				info[k] = v
			}
		}
	}
	var infoError error
	number := func(key string) int64 {
		value, err := capacityInfoNumber(info, key)
		if err != nil {
			infoError = err
		}
		return value
	}
	text := func(key string) string {
		if v, ok := info[key]; ok {
			return v
		}
		return "unknown"
	}
	s.Redis = map[string]any{"used_memory_bytes": number("used_memory"), "used_memory_rss_bytes": number("used_memory_rss"), "maxmemory_bytes": number("maxmemory"), "maxmemory_policy": text("maxmemory_policy"), "rdb_last_bgsave_status": text("rdb_last_bgsave_status"), "aof_enabled": number("aof_enabled"), "evicted_keys_total": number("evicted_keys"), "error_replies_total": number("total_error_replies")}
	if infoError != nil {
		return s, infoError
	}
	for _, count := range counts {
		s.DBKeys += count
	}
	s.Duration = clock().Sub(start).Seconds()
	return s, nil
}

func capacityInfoNumber(info map[string]string, key string) (int64, error) {
	raw, exists := info[key]
	if !exists {
		return 0, nil
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return 0, errors.New("invalid capacity Redis numeric information")
	}
	return value, nil
}
func capacityPrometheus(s capacitySnapshot) string {
	var b strings.Builder
	fmt.Fprintf(&b, "jobseek_redis_capacity_snapshot_unixtime %v\njobseek_redis_capacity_scan_duration_seconds %v\njobseek_redis_capacity_db_keys %d\njobseek_redis_scrape_config_guard_items %d\n", s.Timestamp, s.Duration, s.DBKeys, s.Guarded)
	fmt.Fprintf(&b, "jobseek_redis_capacity_used_memory_bytes %v\njobseek_redis_capacity_maxmemory_bytes %v\n", s.Redis["used_memory_bytes"], s.Redis["maxmemory_bytes"])
	for _, name := range []string{"reachable", "orphan", "reachable_upper_bound", "orphan_lower_bound"} {
		if n, ok := s.ScrapeState[name]; ok {
			fmt.Fprintf(&b, "jobseek_redis_scrape_config_state_keys{state=%q} %d\n", name, n)
		}
	}
	for _, row := range s.Families {
		for _, m := range []struct {
			Name  string
			Value int64
		}{{"keys", row.Keys}, {"items", row.Items}, {"estimated_bytes", row.EstimatedBytes}, {"budget_keys", row.BudgetKeys}, {"budget_items", row.BudgetItems}, {"budget_bytes", row.BudgetBytes}} {
			fmt.Fprintf(&b, "jobseek_redis_key_family_%s{family=%q} %d\n", m.Name, row.Name, m.Value)
		}
	}
	return b.String()
}
func runCapacity(args []string) error {
	o, e := parseCapacityOptions(args)
	if e != nil {
		return e
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, 15*time.Minute)
	defer cancel()
	u := os.Getenv("REDIS_URL")
	if u == "" {
		u = "redis://localhost:6379/0"
	}
	options, e := redis.ParseURL(u)
	if e != nil {
		return errors.New("invalid capacity Redis configuration")
	}
	options.MaxRetries = -1
	options.DialTimeout = 5 * time.Second
	options.ReadTimeout = 10 * time.Second
	options.ContextTimeoutEnabled = true
	c := redis.NewClient(options)
	defer c.Close()
	if o.Action == "rebuild" {
		return runCapacityRebuild(ctx, c, o)
	}
	if o.Action == "prune" {
		r, e := capacityPrune(ctx, c, o)
		if e != nil {
			return e
		}
		return json.NewEncoder(os.Stdout).Encode(r)
	}
	s, e := capacityInventory(ctx, c, o.Action == "inspect", time.Now)
	if e != nil {
		return e
	}
	if o.Format == "prometheus" {
		_, e = fmt.Fprint(os.Stdout, capacityPrometheus(s))
		return e
	}
	return json.NewEncoder(os.Stdout).Encode(s)
}
