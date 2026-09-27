package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

type reaperFixtureBackend struct {
	calls                   []string
	failSweep, failClassify bool
}

func (f *reaperFixtureBackend) Sweep(_ context.Context, wtype string) (leaseReapResult, error) {
	f.calls = append(f.calls, wtype)
	if f.failSweep && wtype == "simple" {
		return leaseReapResult{}, errors.New("Redis failure")
	}
	return leaseReapResult{Reenqueued: 1}, nil
}
func (f *reaperFixtureBackend) Depths(_ context.Context, wtype string) (int64, int64, error) {
	return 2, 3, nil
}
func (f *reaperFixtureBackend) Classify(context.Context) (map[string]map[string]int, error) {
	f.calls = append(f.calls, "classify")
	if f.failClassify {
		return nil, errors.New("DB failure")
	}
	return map[string]map[string]int{"simple": {"actionable": 1}}, nil
}
func TestLeaseReaperContinuesAfterLaneAndObservationFailure(t *testing.T) {
	fixture := &reaperFixtureBackend{failSweep: true, failClassify: true}
	events := []leaseReaperEvent{}
	if err := leaseReaperTick(context.Background(), fixture, func(event leaseReaperEvent) error { events = append(events, event); return nil }); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(fixture.calls, []string{"simple", "browser", "classify"}) || len(events) != 3 || !events[0].Failed || events[1].Failed || !events[2].Failed || events[2].Counts != nil || events[1].Result.Reenqueued != 1 || *events[1].Inflight != 2 {
		t.Fatalf("unexpected tick: %+v %+v", fixture.calls, events)
	}
}
func TestLeaseReaperWaitsThenCancelsAndRejectsBrokenOutput(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	fixture := &reaperFixtureBackend{}
	started := time.Now()
	events := 0
	err := leaseReaperLoop(ctx, 20*time.Millisecond, fixture, func(leaseReaperEvent) error {
		events++
		if events == 3 {
			cancel()
		}
		return nil
	})
	if !errors.Is(err, context.Canceled) || events != 3 || time.Since(started) < 20*time.Millisecond {
		t.Fatalf("invalid cancellation: events=%d err=%v", events, err)
	}
	want := errors.New("output closed")
	if err := leaseReaperTick(context.Background(), fixture, func(leaseReaperEvent) error { return want }); !errors.Is(err, want) {
		t.Fatal("ignored broken parent output")
	}
}
func TestLeaseReaperSettings(t *testing.T) {
	t.Setenv("REAPER_INTERVAL_SECONDS", "0")
	t.Setenv("REAPER_BATCH_SIZE", "200")
	t.Setenv("REAPER_MAX_STRIKES", "5")
	settings, err := loadLeaseReaperSettings()
	if err != nil || settings.Interval != time.Second || settings.BatchSize != 200 || settings.MaxStrikes != 5 {
		t.Fatalf("settings: %+v %v", settings, err)
	}
	t.Setenv("REAPER_BATCH_SIZE", "0")
	if _, err := loadLeaseReaperSettings(); err == nil {
		t.Fatal("accepted unbounded batch")
	}
}

type reaperKey struct {
	Type  string `json:"type"`
	Value any    `json:"value"`
}

func reaperRedisFixture(t *testing.T, ctx context.Context) *redis.Client {
	t.Helper()
	binary, err := exec.LookPath("redis-server")
	if err != nil {
		if os.Getenv("GO_TYPESENSE_TEST_DATABASE_URL") != "" {
			t.Fatal("redis-server required in integration CI")
		}
		t.Skip("redis-server not installed")
	}
	directory, err := os.MkdirTemp("/tmp", "jobseek-reap-")
	if err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(directory, "redis.sock")
	cmd := exec.CommandContext(ctx, binary, "--port", "0", "--unixsocket", socket, "--save", "", "--appendonly", "no")
	cmd.Env = append(os.Environ(), "LC_ALL=C")
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	client := redis.NewClient(&redis.Options{Network: "unix", Addr: socket, DisableIdentity: true, MaxRetries: -1})
	t.Cleanup(func() { client.Close(); _ = cmd.Process.Kill(); _ = cmd.Wait(); os.RemoveAll(directory) })
	for {
		if client.Ping(ctx).Err() == nil {
			break
		}
		if ctx.Err() != nil {
			t.Fatal("Redis fixture did not start")
		}
		time.Sleep(20 * time.Millisecond)
	}
	return client
}
func seedReaperKeys(ctx context.Context, client *redis.Client, keys map[string]reaperKey) error {
	if err := client.FlushDB(ctx).Err(); err != nil {
		return err
	}
	for key, item := range keys {
		var err error
		switch item.Type {
		case "zset":
			for member, score := range item.Value.(map[string]any) {
				if err = client.ZAdd(ctx, key, redis.Z{Member: member, Score: score.(float64)}).Err(); err != nil {
					return err
				}
			}
		case "hash":
			err = client.HSet(ctx, key, item.Value).Err()
		case "string":
			err = client.Set(ctx, key, item.Value, 0).Err()
		default:
			return errors.New("unknown fixture key type")
		}
		if err != nil {
			return err
		}
	}
	return nil
}
func snapshotReaperKeys(ctx context.Context, client *redis.Client) (map[string]reaperKey, error) {
	keys, err := client.Keys(ctx, "*").Result()
	if err != nil {
		return nil, err
	}
	state := map[string]reaperKey{}
	for _, key := range keys {
		kind, err := client.Type(ctx, key).Result()
		if err != nil {
			return nil, err
		}
		var value any
		switch kind {
		case "zset":
			items, e := client.ZRangeWithScores(ctx, key, 0, -1).Result()
			err = e
			values := map[string]any{}
			for _, item := range items {
				values[item.Member.(string)] = item.Score
			}
			value = values
		case "hash":
			items, e := client.HGetAll(ctx, key).Result()
			err = e
			values := map[string]any{}
			for key, item := range items {
				values[key] = item
			}
			value = values
		case "string":
			value, err = client.Get(ctx, key).Result()
		default:
			return nil, fmt.Errorf("unexpected fixture key type %s", kind)
		}
		if err != nil {
			return nil, err
		}
		state[key] = reaperKey{Type: kind, Value: value}
	}
	return state, nil
}
func TestLeaseReaperRedisPythonStateParity(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := reaperRedisFixture(t, ctx)
	data, err := os.ReadFile("testdata/lease_reaper_fixture.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name       string               `json:"name"`
		Wtype      string               `json:"wtype"`
		Now        float64              `json:"now"`
		Batch      int                  `json:"batch"`
		MaxStrikes int                  `json:"max_strikes"`
		Seed       map[string]reaperKey `json:"seed"`
		Steps      []struct {
			Failed bool                 `json:"failed"`
			Result leaseReapResult      `json:"result"`
			State  map[string]reaperKey `json:"state"`
		} `json:"steps"`
	}
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for _, test := range cases {
		t.Run(test.Name, func(t *testing.T) {
			if err := seedReaperKeys(ctx, client, test.Seed); err != nil {
				t.Fatal(err)
			}
			for step, want := range test.Steps {
				result, err := reapLeasesAt(ctx, client, test.Wtype, test.Now, leaseReaperSettings{BatchSize: test.Batch, MaxStrikes: test.MaxStrikes})
				if (err != nil) != want.Failed || err == nil && result != want.Result {
					t.Fatalf("step %d result %+v err %v; want %+v failed %v", step, result, err, want.Result, want.Failed)
				}
				state, err := snapshotReaperKeys(ctx, client)
				if err != nil {
					t.Fatal(err)
				}
				if !reflect.DeepEqual(state, want.State) {
					got, _ := json.Marshal(state)
					expected, _ := json.Marshal(want.State)
					t.Fatalf("step %d Redis effects differ\ngot %s\nwant %s", step, got, expected)
				}
			}
		})
	}
}

func exerciseLiveLeaseReaper(t *testing.T, ctx context.Context, pool *pgxpool.Pool, client *redis.Client) {
	t.Helper()
	if err := client.FlushDB(ctx).Err(); err != nil {
		t.Fatal(err)
	}
	id := "abcdef00-0000-0000-0000-000000000001"
	member := "monitor|example.test|" + id
	if err := client.HSet(ctx, "board:"+id, map[string]string{"domain": "example.test", "board_url": "https://example.test/careers", "crawler_type": "dom"}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := client.ZAdd(ctx, "inflight:simple", redis.Z{Member: member, Score: 1}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := client.HSet(ctx, "inflight_strikes:simple", member, "4").Err(); err != nil {
		t.Fatal(err)
	}
	var events []leaseReaperEvent
	backend := redisLeaseReaper{client: client, pool: pool, settings: leaseReaperSettings{BatchSize: 200, MaxStrikes: 5}}
	if err := leaseReaperTick(ctx, backend, func(event leaseReaperEvent) error { events = append(events, event); return nil }); err != nil {
		t.Fatal(err)
	}
	if len(events) != 3 || events[0].Failed || events[0].Result.DeadLettered != 1 || *events[0].Inflight != 0 || *events[0].Deadletters != 1 || events[2].Failed || events[2].Counts["simple"]["retired"] != 1 {
		t.Fatalf("live recovery and lifecycle join: %+v", events)
	}
}
