package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"os"
	"os/signal"
	"regexp"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

// These are the exact existing Python migration/retry contracts. The offline
// parity test binds each asset to its original source; no Python is loaded by
// the installed executable. The deploy caller keeps the entire lane quiesced.
//
//go:embed provider-cutover/nw.sql
var nwProviderCutoverSQL string

//go:embed provider-cutover/umantis.sql
var umantisProviderCutoverSQL string

//go:embed provider-cutover/umantis-postings.sql
var umantisPostingQuery string

//go:embed provider-cutover/umantis-boards.sql
var umantisBoardQuery string

//go:embed provider-cutover/umantis-scrapes.lua
var umantisScrapeRepairLua string

//go:embed provider-cutover/umantis-park.lua
var umantisParkMonitorsLua string

type umantisPosting struct{ ID, BoardID, URL string }
type umantisBoard struct{ ID, Domain string }
type umantisCutoverSummary struct {
	Postings int   `json:"postings"`
	Changed  int64 `json:"redis_hashes_changed"`
	Parked   int64 `json:"monitor_entries_parked"`
}

func reapplyNWProviderCutover(ctx context.Context, conn *pgx.Conn) error {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	if _, err := conn.Exec(ctx, nwProviderCutoverSQL); err != nil {
		return errors.New("NW provider identity repair failed")
	}
	return nil
}

func umantisRepairBatch(ctx context.Context, client *redis.Client, postings []umantisPosting) (int64, error) {
	keys := make([]string, 0, len(postings))
	args := make([]any, 0, len(postings)*2)
	for _, p := range postings {
		keys = append(keys, "scrape:"+p.ID)
		args = append(args, p.BoardID, p.URL)
	}
	n, err := client.Eval(ctx, umantisScrapeRepairLua, keys, args...).Int64()
	if err != nil || n < 0 || n > int64(len(postings)) {
		return 0, errors.New("Umantis scrape repair was not acknowledged")
	}
	return n, nil
}

func repairUmantisProviderCutover(ctx context.Context, conn *pgx.Conn, client *redis.Client, park bool) (umantisCutoverSummary, error) {
	var result umantisCutoverSummary
	// The original receipt validator must finish before any Redis effect.
	validate, cancel := context.WithTimeout(ctx, 60*time.Second)
	_, err := conn.Exec(validate, umantisProviderCutoverSQL)
	cancel()
	if err != nil {
		return result, errors.New("Umantis provider identity validation failed")
	}
	query, cancel := context.WithTimeout(ctx, 60*time.Second)
	rows, err := conn.Query(query, umantisPostingQuery)
	if err != nil {
		cancel()
		return result, errors.New("Umantis canonical posting query failed")
	}
	postings, err := pgx.CollectRows(rows, pgx.RowToStructByPos[umantisPosting])
	cancel()
	if err != nil {
		return result, errors.New("Umantis canonical posting inventory failed")
	}
	result.Postings = len(postings)
	// Preserve both original passes and 500-row atomic Lua batches. A later
	// failed batch can leave earlier batches complete; exact retry resumes.
	for pass := 0; pass < 2; pass++ {
		for start := 0; start < len(postings); start += 500 {
			n, err := umantisRepairBatch(ctx, client, postings[start:min(start+500, len(postings))])
			if err != nil {
				return result, err
			}
			if pass == 1 && n != 0 {
				return result, errors.New("Umantis Redis identity verification changed unexpected state")
			}
			if pass == 0 {
				result.Changed += n
			}
		}
	}
	if !park {
		return result, nil
	}
	query, cancel = context.WithTimeout(ctx, 60*time.Second)
	rows, err = conn.Query(query, umantisBoardQuery)
	if err != nil {
		cancel()
		return result, errors.New("Umantis rollback board query failed")
	}
	boards, err := pgx.CollectRows(rows, pgx.RowToStructByPos[umantisBoard])
	cancel()
	if err != nil {
		return result, errors.New("Umantis rollback board inventory failed")
	}
	for _, b := range boards {
		if b.Domain == "" {
			return result, errors.New("Umantis rollback board has no exact throttle key")
		}
	}
	for start := 0; start < len(boards); start += 500 {
		args := make([]any, 0, 1000)
		for _, b := range boards[start:min(start+500, len(boards))] {
			args = append(args, b.ID, b.Domain)
		}
		n, err := client.Eval(ctx, umantisParkMonitorsLua, []string{}, args...).Int64()
		if err != nil || n < 0 {
			return result, errors.New("Umantis monitor parking was not acknowledged")
		}
		result.Parked += n
	}
	return result, nil
}

var providerCutoverRole = regexp.MustCompile(`^[a-z0-9-]{1,40}$`)

func providerCutoverPoolConfig(dsn, role string) (*pgxpool.Config, error) {
	if !providerCutoverRole.MatchString(role) {
		return nil, errors.New("invalid provider cutover database role")
	}
	config, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, errors.New("invalid local database configuration")
	}
	config.MinConns, config.MaxConns = 0, 1
	config.ConnConfig.ConnectTimeout = 5 * time.Second
	config.ConnConfig.RuntimeParams["application_name"] = "jobseek:crawler:" + role + ":local"
	config.ConnConfig.RuntimeParams["statement_timeout"] = "30s"
	config.ConnConfig.RuntimeParams["idle_in_transaction_session_timeout"] = "60s"
	config.ConnConfig.RuntimeParams["tcp_keepalives_idle"] = "60"
	config.ConnConfig.RuntimeParams["tcp_keepalives_interval"] = "10"
	config.ConnConfig.RuntimeParams["tcp_keepalives_count"] = "3"
	return config, nil
}

func runProviderCutover(kind string, park bool) error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	dsn := os.Getenv("LOCAL_DATABASE_URL")
	if dsn == "" {
		return errors.New("LOCAL_DATABASE_URL is required")
	}
	role := os.Getenv("CRAWLER_DB_ROLE")
	if role == "" {
		role = "deploy-" + kind + "-provider-cutover"
	}
	config, err := providerCutoverPoolConfig(dsn, role)
	if err != nil {
		return err
	}
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return errors.New("open provider cutover database failed")
	}
	defer pool.Close()
	conn, err := pool.Acquire(ctx)
	if err != nil {
		return errors.New("acquire provider cutover database failed")
	}
	defer conn.Release()
	if kind == "nw" {
		return reapplyNWProviderCutover(ctx, conn.Conn())
	}
	endpoint := os.Getenv("REDIS_URL")
	if endpoint == "" {
		endpoint = "redis://localhost:6379/0"
	}
	options, err := redis.ParseURL(endpoint)
	if err != nil {
		return errors.New("invalid Redis configuration")
	}
	options.Protocol, options.MaxRetries = 2, -1
	options.ContextTimeoutEnabled = true
	options.DialTimeout, options.ReadTimeout, options.WriteTimeout = 5*time.Second, 10*time.Second, 10*time.Second
	client := redis.NewClient(options)
	defer client.Close()
	result, err := repairUmantisProviderCutover(ctx, conn.Conn(), client, park)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(result)
}
