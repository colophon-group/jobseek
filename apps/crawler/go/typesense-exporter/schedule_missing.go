package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"os"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

//go:embed schedule_missing.lua
var missingSchedulePrelude string

func missingScheduleProgram() string {
	return missingSchedulePrelude + "\nlocal function original_enqueue()\n" + deadletterEnqueueLua + "\nend\nlocal restored=original_enqueue()\nif restored==1 then redis.call('SETNX','delay:'..repair_domain,repair_delay) end\nreturn restored\n"
}

const missingCacheScanLua = `local out={};for _,id in ipairs(ARGV) do local key='scrape:'..id;local kind=redis.call('TYPE',key)['ok'];if kind~='none' and kind~='hash' then return redis.error_reply('invalid canonical schedule cache') end;if kind=='none' then out[#out+1]=id end end;return out`
const missingOwnershipLua = `if redis.call('EXISTS','ordinary:ownership:active')~=0 or redis.call('EXISTS','lightpanda-b0:producer-owner')~=0 then return 0 end;local kind=redis.call('TYPE','lightpanda-b0:legacy-guard')['ok'];if kind~='none' and kind~='hash' then return 0 end;if redis.call('HLEN','lightpanda-b0:legacy-guard')~=0 then return 0 end;return 1`

func missingScheduleAuthority(ctx context.Context, pool *pgxpool.Pool, c *redis.Client) (int64, error) {
	var epoch int64
	var called, owned bool
	err := pool.QueryRow(ctx, `SELECT last_value,is_called,EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active') FROM public.lightpanda_b0_routing_epoch_seq`).Scan(&epoch, &called, &owned)
	if err != nil || !called || epoch < 1 || owned {
		return 0, errors.New("missing schedule canonical ownership unavailable")
	}
	n, err := c.EvalRO(ctx, missingOwnershipLua, nil).Int()
	if err != nil || n != 1 {
		return 0, errors.New("missing schedule Redis ownership unavailable")
	}
	return epoch, nil
}

func missingSchedulePage(ctx context.Context, pool *pgxpool.Pool, o capacityOptions) ([]capacitySchedule, error) {
	// Retain the documented canonical rebuild eligibility; additionally exclude
	// disabled/reserved boards and unresolved live SQL attempts.
	query := strings.Replace(capacityRebuildSQL, "jb.scraper_needs_browser", "jb.scraper_needs_browser, COALESCE(jb.scrape_interval_hours,24)", 1)
	query = strings.Replace(query, "WHERE jp.is_active", "WHERE jp.is_active AND jb.is_enabled AND NOT jb.tdm_reserved AND (jp.leased_until IS NULL OR jp.leased_until<=now())", 1)
	tx, err := pool.BeginTx(ctx, pgx.TxOptions{AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, errors.New("open missing schedule snapshot failed")
	}
	defer tx.Rollback(context.Background())
	var cursor any
	if o.AfterID != "" {
		cursor = o.AfterID
	}
	result, err := tx.Query(ctx, query, cursor, o.Limit)
	if err != nil {
		return nil, errors.New("read missing schedule candidates failed")
	}
	rows := []capacitySchedule{}
	for result.Next() {
		var row capacitySchedule
		var browser *bool
		if err = result.Scan(&row.ID, &row.URL, &row.Board, &row.Hash, &row.Due, &browser, &row.IntervalHours); err != nil {
			result.Close()
			return nil, errors.New("decode missing schedule candidate failed")
		}
		row.Browser = browser != nil && *browser
		rows = append(rows, row)
	}
	err = result.Err()
	result.Close()
	if err != nil || tx.Commit(ctx) != nil {
		return nil, errors.New("complete missing schedule snapshot failed")
	}
	return rows, nil
}

func missingScheduleCandidates(ctx context.Context, c *redis.Client, rows []capacitySchedule) ([]capacitySchedule, error) {
	if len(rows) == 0 {
		return nil, nil
	}
	args := make([]any, len(rows))
	byID := make(map[string]capacitySchedule, len(rows))
	for i, row := range rows {
		args[i] = row.ID
		byID[row.ID] = row
	}
	ids, err := c.EvalRO(ctx, missingCacheScanLua, nil, args...).StringSlice()
	if err != nil {
		return nil, errors.New("inspect missing schedule caches failed")
	}
	missing := make([]capacitySchedule, 0, len(ids))
	seen := map[string]bool{}
	for _, id := range ids {
		row, ok := byID[id]
		if !ok || seen[id] {
			return nil, errors.New("invalid missing schedule inspection")
		}
		seen[id] = true
		missing = append(missing, row)
	}
	return missing, nil
}

func runMissingScheduleRepair(ctx context.Context, c *redis.Client, o capacityOptions) error {
	if o.Limit < 1 || o.Limit > 50000 {
		return errors.New("invalid missing schedule batch bound")
	}
	o.AfterID = strings.ToLower(o.AfterID)
	mode := os.Getenv("LIGHTPANDA_B0_PRODUCER_MODE")
	if mode != "" && mode != "off" {
		return errors.New("missing schedule repair requires dormant producer")
	}
	producer := capacityProducer{mode: "off", socket: capacityProducerSocket, uid: 10001}
	if err := capacityProducerDormant("/run/jobseek-lightpanda-producer", producer.uid); err != nil {
		return err
	}
	pool, err := openPool(ctx)
	if err != nil {
		return err
	}
	defer pool.Close()
	epoch, err := missingScheduleAuthority(ctx, pool, c)
	if err != nil {
		return err
	}
	delays, err := loadDeadletterDelays()
	if err != nil {
		return err
	}
	scanned, candidates, restored, preserved := 0, 0, 0, 0
	for {
		rows, err := missingSchedulePage(ctx, pool, o)
		if err != nil {
			return err
		}
		scanned += len(rows)
		if scanned > 5000000 {
			return errors.New("missing schedule repair exceeds canonical bound")
		}
		missing, err := missingScheduleCandidates(ctx, c, rows)
		if err != nil {
			return err
		}
		candidates += len(missing)
		if o.Apply {
			report, err := capacityRebuildSchedules(ctx, c, missing, o, producer, delays)
			if err != nil {
				return err
			}
			restored += report.NewlyEnqueued
			preserved += report.AlreadyPresent
		} else {
			for _, row := range missing {
				domain, score, config, err := capacityScheduleValues(row)
				if err != nil {
					return err
				}
				worker, first := "simple", "0"
				if row.Browser {
					worker = "browser"
				}
				if row.Hash == nil {
					first = "1"
				}
				args := []any{worker, domain, row.ID, strconv.FormatFloat(score, 'f', -1, 64), "scrape", first, "0"}
				for _, field := range []string{"source_url", "board_id", "description_r2_hash", "scrape_step"} {
					args = append(args, field, config[field])
				}
				if interval, ok := config["scrape_interval_hours"]; ok {
					args = append(args, "scrape_interval_hours", interval)
				}
				n, err := c.EvalRO(ctx, missingScheduleProgram(), []string{"inspect", strconv.FormatFloat(delays.forDomain(domain), 'f', -1, 64)}, args...).Int()
				if err != nil || n < 0 || n > 1 {
					return errors.New("inspect missing schedule lifecycle failed")
				}
				restored += n
				preserved += 1 - n
			}
		}
		current, err := missingScheduleAuthority(ctx, pool, c)
		if err != nil || current != epoch {
			return errors.New("missing schedule ownership changed")
		}
		if len(rows) < o.Limit {
			break
		}
		next := rows[len(rows)-1].ID
		if next <= o.AfterID {
			return errors.New("missing schedule cursor did not advance")
		}
		o.AfterID = next
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"dry_run": !o.Apply, "canonical_candidates_scanned": scanned, "absent_cache_candidates": candidates, "reconstructed_or_would_reconstruct": restored, "existing_lifecycle_preserved": preserved, "complete": true, "routing_epoch": epoch})
}
