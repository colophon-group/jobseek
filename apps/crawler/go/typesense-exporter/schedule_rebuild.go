package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"math"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

//go:embed schedule_rebuild.sql
var capacityRebuildSQL string

var capacityUUID = regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

type capacitySchedule struct {
	ID, URL, Board string
	Hash           *string
	Due            time.Time
	Browser        bool
	IntervalHours  int
}

type capacityRebuildReport struct {
	DryRun         bool    `json:"dry_run"`
	AfterID        *string `json:"after_id"`
	NextAfterID    *string `json:"next_after_id"`
	Selected       int     `json:"selected"`
	NewlyEnqueued  int     `json:"newly_enqueued"`
	AlreadyPresent int     `json:"already_present"`
	Complete       bool    `json:"complete"`
}

func capacityScheduleValues(s capacitySchedule) (string, float64, map[string]string, error) {
	u, err := url.Parse(s.URL)
	if err != nil {
		return "", 0, nil, errors.New("invalid canonical schedule URL")
	}
	domain := strings.ToLower(u.Hostname())
	score := float64(s.Due.Unix()) + float64(s.Due.Nanosecond())/1e9
	hash := ""
	if s.Hash != nil {
		hash = *s.Hash
	} else {
		score = 0
	}
	if math.IsNaN(score) || math.IsInf(score, 0) {
		return "", 0, nil, errors.New("invalid canonical schedule time")
	}
	config := map[string]string{"source_url": s.URL, "board_id": s.Board, "description_r2_hash": hash, "scrape_step": "0"}
	if s.IntervalHours != 0 {
		if s.IntervalHours < 1 || s.IntervalHours > 2562047 {
			return "", 0, nil, errors.New("invalid canonical scrape interval")
		}
		config["scrape_interval_hours"] = strconv.Itoa(s.IntervalHours)
	}
	return domain, score, config, nil
}

// The producer authenticates every row before any legacy pipeline executes.
// Only its literal legacy decision authorizes the original enqueue script.
func capacityRebuildSchedules(ctx context.Context, c *redis.Client, rows []capacitySchedule, o capacityOptions, producer capacityProducer, delay deadletterDelays) (capacityRebuildReport, error) {
	if o.Action == "repair-missing" && producer.mode != "" && producer.mode != "off" {
		return capacityRebuildReport{}, errors.New("missing schedule repair cannot use mixed ownership")
	}
	report := capacityRebuildReport{DryRun: !o.Apply, Selected: len(rows), Complete: len(rows) < o.Limit}
	if o.AfterID != "" {
		cursor := o.AfterID
		report.AfterID, report.NextAfterID = &cursor, &cursor
	}
	if len(rows) != 0 {
		cursor := rows[len(rows)-1].ID
		report.NextAfterID = &cursor
	}
	if !o.Apply {
		return report, nil
	}
	legacy := make([]capacitySchedule, 0, len(rows))
	for _, row := range rows {
		domain, score, config, err := capacityScheduleValues(row)
		if err != nil {
			return report, err
		}
		owned, added, err := producer.enqueue(ctx, domain, score, row, config)
		if err != nil {
			return report, err
		}
		if !owned {
			legacy = append(legacy, row)
			continue
		}
		if err := c.Set(ctx, "delay:"+domain, strconv.FormatFloat(delay.forDomain(domain), 'f', -1, 64), 0).Err(); err != nil {
			return report, errors.New("producer schedule delay update failed")
		}
		if added {
			report.NewlyEnqueued++
		} else {
			report.AlreadyPresent++
		}
	}
	if len(legacy) == 0 {
		return report, nil
	}
	program := deadletterEnqueueLua
	if o.Action == "repair-missing" {
		program = missingScheduleProgram()
	}
	sha, err := c.ScriptLoad(ctx, program).Result()
	if err != nil {
		return report, errors.New("load original schedule enqueue failed")
	}
	for start := 0; start < len(legacy); start += 1000 {
		batch := legacy[start:min(start+1000, len(legacy))]
		pipe := c.Pipeline()
		commands := make([]*redis.Cmd, 0, len(batch))
		domains := map[string]bool{}
		now := strconv.FormatFloat(float64(time.Now().UnixNano())/1e9, 'f', -1, 64)
		for _, row := range batch {
			domain, score, config, err := capacityScheduleValues(row)
			if err != nil {
				return report, err
			}
			worker, first := "simple", "0"
			if row.Browser {
				worker = "browser"
			}
			if row.Hash == nil {
				first = "1"
			}
			args := []any{worker, domain, row.ID, strconv.FormatFloat(score, 'f', -1, 64), "scrape", first, now}
			for _, field := range []string{"source_url", "board_id", "description_r2_hash", "scrape_step"} {
				args = append(args, field, config[field])
			}
			var keys []string
			if o.Action == "repair-missing" {
				keys = []string{"apply", strconv.FormatFloat(delay.forDomain(domain), 'f', -1, 64)}
			}
			if interval, ok := config["scrape_interval_hours"]; ok {
				args = append(args, "scrape_interval_hours", interval)
			}
			commands = append(commands, pipe.EvalSha(ctx, sha, keys, args...))
			domains[domain] = true
		}
		ordered := make([]string, 0, len(domains))
		for domain := range domains {
			ordered = append(ordered, domain)
		}
		sort.Strings(ordered)
		for _, domain := range ordered {
			if o.Action == "repair-missing" {
				continue
			}
			pipe.Set(ctx, "delay:"+domain, strconv.FormatFloat(delay.forDomain(domain), 'f', -1, 64), 0)
		}
		if _, err := pipe.Exec(ctx); err != nil {
			return report, errors.New("schedule enqueue pipeline failed")
		}
		for _, command := range commands {
			n, err := command.Int()
			if err != nil || n != 0 && n != 1 {
				return report, errors.New("invalid schedule enqueue outcome")
			}
			if n == 1 {
				report.NewlyEnqueued++
			} else {
				report.AlreadyPresent++
			}
		}
	}
	return report, nil
}

func runCapacityRebuild(ctx context.Context, c *redis.Client, o capacityOptions) error {
	delay, err := loadDeadletterDelays()
	if err != nil {
		return err
	}
	pool, err := openPool(ctx)
	if err != nil {
		return err
	}
	defer pool.Close()
	tx, err := pool.BeginTx(ctx, pgx.TxOptions{AccessMode: pgx.ReadOnly})
	if err != nil {
		return errors.New("open canonical schedule snapshot failed")
	}
	defer tx.Rollback(context.Background())
	var cursor any
	if o.AfterID != "" {
		cursor = o.AfterID
	}
	result, err := tx.Query(ctx, capacityRebuildSQL, cursor, o.Limit)
	if err != nil {
		return errors.New("select canonical schedules failed")
	}
	rows := []capacitySchedule{}
	for result.Next() {
		var row capacitySchedule
		var browser *bool
		if err := result.Scan(&row.ID, &row.URL, &row.Board, &row.Hash, &row.Due, &browser); err != nil {
			result.Close()
			return errors.New("decode canonical schedule failed")
		}
		row.Browser = browser != nil && *browser
		rows = append(rows, row)
	}
	err = result.Err()
	result.Close()
	if err != nil || tx.Commit(ctx) != nil {
		return errors.New("canonical schedule read failed")
	}
	producer := capacityProducer{mode: os.Getenv("LIGHTPANDA_B0_PRODUCER_MODE"), socket: capacityProducerSocket, uid: 10001}
	if producer.mode == "" {
		producer.mode = "off"
	}
	if configured := os.Getenv("LIGHTPANDA_B0_PRODUCER_SOCKET"); producer.mode == "enabled" && configured != "" && configured != producer.socket {
		return errors.New("producer socket configuration differs")
	}
	report, err := capacityRebuildSchedules(ctx, c, rows, o, producer, delay)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(report)
}
