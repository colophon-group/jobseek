package main

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
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

//go:embed board_sync_remove.lua
var boardSyncRemoveLua string

const boardSyncInputLimit = 128 << 20

type boardSyncSchedule struct {
	Domain      string            `json:"domain"`
	BoardID     string            `json:"board_id"`
	NextCheckAt float64           `json:"next_check_at"`
	Config      map[string]string `json:"config"`
	Browser     bool              `json:"browser"`
	FirstTime   bool              `json:"first_time"`
}
type boardSyncInput struct {
	Schedules []boardSyncSchedule `json:"schedules"`
	Orphans   [][]string          `json:"orphans"`
}

func decodeBoardSync(reader io.Reader) (boardSyncInput, error) {
	var input boardSyncInput
	body, err := io.ReadAll(io.LimitReader(reader, boardSyncInputLimit+1))
	if err != nil || len(body) > boardSyncInputLimit {
		return input, errors.New("board sync input exceeds limit or could not be read")
	}
	if bytes.Equal(bytes.TrimSpace(body), []byte("null")) {
		return input, errors.New("null board sync input")
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&input); err != nil {
		return input, errors.New("invalid board sync input")
	}
	if decoder.Decode(new(any)) != io.EOF {
		return input, errors.New("unexpected trailing board sync input")
	}
	if len(input.Schedules) > 100000 || len(input.Orphans) > 100000 {
		return input, errors.New("too many board sync effects")
	}
	for _, s := range input.Schedules {
		if s.Domain == "" || s.BoardID == "" || math.IsNaN(s.NextCheckAt) || math.IsInf(s.NextCheckAt, 0) {
			return input, errors.New("invalid board sync schedule")
		}
	}
	for _, pair := range input.Orphans {
		if len(pair) != 2 || pair[0] == "" || pair[1] == "" {
			return input, errors.New("invalid board sync orphan")
		}
	}
	return input, nil
}

// Keep Python's float string representation for Redis string values. Redis
// score comparisons use numbers; the delay keys also retain exact bytes.
func boardSyncFloat(value float64) string {
	format := byte('f')
	if value != 0 && (math.Abs(value) < 1e-4 || math.Abs(value) >= 1e16) {
		format = 'e'
	}
	result := strconv.FormatFloat(value, format, -1, 64)
	if !strings.ContainsAny(result, ".e") {
		result += ".0"
	}
	return result
}

func applyBoardSync(ctx context.Context, client *redis.Client, input boardSyncInput, delays deadletterDelays, clock func() time.Time) error {
	if len(input.Schedules) == 0 && len(input.Orphans) == 0 {
		return nil
	}
	enqueueSHA, err := client.ScriptLoad(ctx, deadletterEnqueueLua).Result()
	if err != nil {
		return errors.New("load board enqueue script failed")
	}
	removeSHA, err := client.ScriptLoad(ctx, boardSyncRemoveLua).Result()
	if err != nil {
		return errors.New("load board removal script failed")
	}
	for start := 0; start < len(input.Schedules); start += 1000 {
		batch := input.Schedules[start:min(start+1000, len(input.Schedules))]
		now := clock()
		stamp := boardSyncFloat(float64(now.Unix()) + float64(now.Nanosecond())/1e9)
		pipe := client.Pipeline()
		for _, s := range batch {
			wtype, first := "simple", "0"
			if s.Browser {
				wtype = "browser"
			}
			if s.FirstTime {
				first = "1"
			}
			pipe.EvalSha(ctx, enqueueSHA, []string{}, wtype, s.Domain, s.BoardID, boardSyncFloat(s.NextCheckAt), "monitor", first, stamp)
			if len(s.Config) > 0 {
				config := make(map[string]string, len(s.Config)+1)
				for k, v := range s.Config {
					config[k] = v
				}
				config["domain"] = s.Domain
				pipe.HSet(ctx, "board:"+s.BoardID, config)
			}
			pipe.Set(ctx, "delay:"+s.Domain, boardSyncFloat(delays.forDomain(s.Domain)), 0)
		}
		// Preserve the existing ordered, nontransactional pipeline. A Redis or
		// transport error fails the sync; never blindly replay ambiguous writes.
		if _, err = pipe.Exec(ctx); err != nil {
			return errors.New("board enqueue batch was not fully acknowledged")
		}
	}
	for start := 0; start < len(input.Orphans); start += 1000 {
		pipe := client.Pipeline()
		for _, pair := range input.Orphans[start:min(start+1000, len(input.Orphans))] {
			pipe.EvalSha(ctx, removeSHA, []string{}, pair[0], pair[1])
		}
		if _, err = pipe.Exec(ctx); err != nil {
			return errors.New("board removal batch was not fully acknowledged")
		}
	}
	return nil
}

func runBoardSync() error {
	input, err := decodeBoardSync(os.Stdin)
	if err != nil {
		return err
	}
	delays, err := loadDeadletterDelays()
	if err != nil {
		return err
	}
	signalCtx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(signalCtx, 5*time.Minute)
	defer cancel()
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
	options.ReadTimeout = 30 * time.Second
	options.WriteTimeout = 30 * time.Second
	options.ContextTimeoutEnabled = true
	options.MaxRetries = -1
	client := redis.NewClient(options)
	defer client.Close()
	if err = applyBoardSync(ctx, client, input, delays, time.Now); err != nil {
		return err
	}
	_, err = fmt.Fprintf(os.Stdout, "{\"event\":\"sync.boards.redis_applied\",\"engine\":\"go\",\"redis_enqueued\":%d,\"redis_orphans_removed\":%d}\n", len(input.Schedules), len(input.Orphans))
	return err
}
