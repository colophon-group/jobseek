package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

//go:embed lease_reaper.lua
var leaseReaperLua string

type leaseReaperSettings struct {
	Interval              time.Duration
	BatchSize, MaxStrikes int
}

func loadLeaseReaperSettings() (leaseReaperSettings, error) {
	values := map[string]int{"REAPER_INTERVAL_SECONDS": 30, "REAPER_BATCH_SIZE": 200, "REAPER_MAX_STRIKES": 5}
	for key, fallback := range values {
		raw := os.Getenv(key)
		if raw == "" {
			values[key] = fallback
			continue
		}
		value, err := strconv.Atoi(raw)
		if err != nil {
			return leaseReaperSettings{}, fmt.Errorf("invalid %s", key)
		}
		values[key] = value
	}
	interval := max(1, values["REAPER_INTERVAL_SECONDS"])
	if int64(interval) > int64((1<<63-1)/time.Second) || values["REAPER_BATCH_SIZE"] < 1 || values["REAPER_MAX_STRIKES"] < 1 {
		return leaseReaperSettings{}, errors.New("invalid lease reaper bounds")
	}
	return leaseReaperSettings{Interval: time.Duration(interval) * time.Second, BatchSize: values["REAPER_BATCH_SIZE"], MaxStrikes: values["REAPER_MAX_STRIKES"]}, nil
}

type leaseReapResult struct {
	Reenqueued    int64 `json:"reenqueued"`
	DeadLettered  int64 `json:"dead_lettered"`
	MissingConfig int64 `json:"missing_config"`
}
type leaseReaperEvent struct {
	Event       string                    `json:"event"`
	Wtype       string                    `json:"wtype,omitempty"`
	Result      *leaseReapResult          `json:"result,omitempty"`
	Inflight    *int64                    `json:"inflight,omitempty"`
	Deadletters *int64                    `json:"deadletters,omitempty"`
	Counts      map[string]map[string]int `json:"counts,omitempty"`
	Failed      bool                      `json:"failed"`
}

type leaseReaperBackend interface {
	Sweep(context.Context, string) (leaseReapResult, error)
	Depths(context.Context, string) (int64, int64, error)
	Classify(context.Context) (map[string]map[string]int, error)
}

type redisLeaseReaper struct {
	client   *redis.Client
	pool     *pgxpool.Pool
	settings leaseReaperSettings
}

func reapLeasesAt(ctx context.Context, client *redis.Client, wtype string, now float64, settings leaseReaperSettings) (leaseReapResult, error) {
	var result leaseReapResult
	if wtype != "simple" && wtype != "browser" {
		return result, errors.New("invalid lease worker type")
	}
	// No blind transport replay: a lost acknowledgement leaves observation to
	// the next normal tick. The same Lua handles task-level idempotence.
	raw, err := client.Eval(ctx, leaseReaperLua, []string{}, wtype, strconv.FormatFloat(now, 'f', -1, 64), settings.BatchSize, settings.MaxStrikes, strconv.FormatFloat(now, 'f', -1, 64)).Slice()
	if err != nil {
		return result, errors.New("lease sweep was not acknowledged")
	}
	if len(raw) != 3 {
		return result, errors.New("invalid lease sweep acknowledgement")
	}
	values := []*int64{&result.Reenqueued, &result.DeadLettered, &result.MissingConfig}
	for i, item := range raw {
		value, ok := item.(int64)
		if !ok || value < 0 || value > int64(settings.BatchSize) {
			return leaseReapResult{}, errors.New("invalid lease sweep count")
		}
		*values[i] = value
	}
	if result.Reenqueued+result.DeadLettered+result.MissingConfig > int64(settings.BatchSize) {
		return leaseReapResult{}, errors.New("invalid lease sweep total")
	}
	return result, nil
}
func (r redisLeaseReaper) Sweep(ctx context.Context, wtype string) (leaseReapResult, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	now, err := r.client.Time(ctx).Result()
	if err != nil {
		return leaseReapResult{}, errors.New("read lease clock failed")
	}
	return reapLeasesAt(ctx, r.client, wtype, float64(now.Unix())+float64(now.Nanosecond())/1e9, r.settings)
}
func (r redisLeaseReaper) Depths(ctx context.Context, wtype string) (int64, int64, error) {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	pipe := r.client.Pipeline()
	inflight := pipe.ZCard(ctx, "inflight:"+wtype)
	dead := pipe.ZCard(ctx, "deadletter:"+wtype)
	if _, err := pipe.Exec(ctx); err != nil {
		return 0, 0, errors.New("read lease depths failed")
	}
	return inflight.Val(), dead.Val(), nil
}
func (r redisLeaseReaper) Classify(ctx context.Context) (map[string]map[string]int, error) {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	snapshot, err := readDeadletterSnapshot(ctx, r.pool, r.client)
	if err != nil {
		return nil, err
	}
	report, err := classifyDeadletterSnapshot(snapshot)
	return report.Counts, err
}
func leaseReaperTick(ctx context.Context, backend leaseReaperBackend, emit func(leaseReaperEvent) error) error {
	for _, wtype := range []string{"simple", "browser"} {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		result, err := backend.Sweep(ctx, wtype)
		event := leaseReaperEvent{Event: "reaper.sweep", Wtype: wtype, Failed: err != nil}
		if err == nil {
			event.Result = &result
			inflight, dead, depthErr := backend.Depths(ctx, wtype)
			if depthErr == nil {
				event.Inflight = &inflight
				event.Deadletters = &dead
			}
		}
		if err := emit(event); err != nil {
			return err
		}
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	counts, err := backend.Classify(ctx)
	event := leaseReaperEvent{Event: "reaper.lifecycle", Counts: counts, Failed: err != nil}
	if err != nil {
		event.Counts = nil
	}
	return emit(event)
}
func leaseReaperLoop(ctx context.Context, interval time.Duration, backend leaseReaperBackend, emit func(leaseReaperEvent) error) error {
	for {
		timer := time.NewTimer(interval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
		if err := leaseReaperTick(ctx, backend, emit); err != nil {
			return err
		}
	}
}
func runLeaseReaper() error {
	settings, err := loadLeaseReaperSettings()
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	dsn := os.Getenv("LOCAL_DATABASE_URL")
	if dsn == "" {
		return errors.New("LOCAL_DATABASE_URL is required")
	}
	config, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return errors.New("invalid local database configuration")
	}
	config.MaxConns = 1
	config.ConnConfig.ConnectTimeout = 5 * time.Second
	config.ConnConfig.RuntimeParams["application_name"] = "jobseek:crawler:lease-reaper|lifecycle:local"
	config.ConnConfig.RuntimeParams["statement_timeout"] = "10000"
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return errors.New("open lease reaper database pool failed")
	}
	defer pool.Close()
	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		redisURL = "redis://localhost:6379/0"
	}
	options, err := redis.ParseURL(redisURL)
	if err != nil {
		return errors.New("invalid Redis configuration")
	}
	options.Protocol = 2
	options.DialTimeout = 3 * time.Second
	options.ReadTimeout = 5 * time.Second
	options.ContextTimeoutEnabled = true
	options.MaxRetries = -1
	client := redis.NewClient(options)
	defer client.Close()
	encoder := json.NewEncoder(os.Stdout)
	err = leaseReaperLoop(ctx, settings.Interval, redisLeaseReaper{client: client, pool: pool, settings: settings}, func(event leaseReaperEvent) error { return encoder.Encode(event) })
	if errors.Is(err, context.Canceled) {
		return nil
	}
	return err
}
