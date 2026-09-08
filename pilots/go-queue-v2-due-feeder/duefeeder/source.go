package duefeeder

import (
	"context"
	"errors"
	"math"
	"strconv"
	"strings"

	queuev2 "github.com/colophon-group/jobseek/apps/crawler/contracts/queue/v2/conformance/go"
	"github.com/redis/go-redis/v9"
)

const (
	// MaxPageSize is deliberately smaller than a production queue batch. The
	// one-shot pilot has no durable cursor and must keep all resolution state
	// bounded in memory before it submits anything.
	MaxPageSize = 256

	maxTaskIDBytes    = 512
	maxRevisionBytes  = 13 // queue-v2 numeric maximum is 9_999_999_999_999.
	maxAggregateBytes = 64 * 1024
)

var (
	ErrInvalidLimit    = errors.New("invalid due snapshot limit")
	ErrInvalidSnapshot = errors.New("invalid due snapshot")
	ErrSourceRead      = errors.New("due snapshot read failed")
)

// Reference is the entire resolver key. Ready scores are intentionally not
// propagated: they are advisory selection metadata, not a lifecycle fence.
type Reference struct {
	TaskID         string
	ConfigRevision int64
}

// DueSource returns an advisory, bounded page of unclaimed references.
type DueSource interface {
	ListDue(context.Context, int) ([]Reference, error)
}

// RedisDueSource reads the ready index and exact configuration revisions. It
// has no write method and executes no Lua script.
type RedisDueSource struct {
	client     *redis.Client
	configsKey string
	readyKey   string
}

// NewRedisDueSource derives the two existing keys from the queue-v2 candidate
// client so a caller cannot accidentally combine namespaces.
func NewRedisDueSource(
	client *redis.Client,
	candidates *queuev2.RedisCandidateClient,
) (*RedisDueSource, error) {
	if client == nil || candidates == nil {
		return nil, ErrInvalidSnapshot
	}
	keys := candidates.Keys()
	if len(keys) != 7 || keys[1] == "" || keys[3] == "" {
		return nil, ErrInvalidSnapshot
	}
	return &RedisDueSource{client: client, configsKey: keys[1], readyKey: keys[3]}, nil
}

// ListDue obtains Redis server time, selects an inclusive due page ordered by
// score then member, and pipelines exact revision reads. Any malformed or
// missing member fails the whole page before a candidate can be resolved.
func (s *RedisDueSource) ListDue(ctx context.Context, limit int) ([]Reference, error) {
	if err := validateLimit(ctx, limit); err != nil {
		return nil, err
	}
	if s == nil || s.client == nil || s.configsKey == "" || s.readyKey == "" {
		return nil, ErrInvalidSnapshot
	}

	serverTime, err := s.client.Time(ctx).Result()
	if err != nil {
		return nil, sourceError(ctx)
	}
	nowMS := serverTime.UnixMilli()
	if nowMS < 1 || nowMS > queuev2.RedisCandidateMaxInteger {
		return nil, ErrInvalidSnapshot
	}

	entries, err := s.client.ZRangeArgsWithScores(ctx, redis.ZRangeArgs{
		Key: s.readyKey, Start: "-inf", Stop: strconv.FormatInt(nowMS, 10),
		ByScore: true, Offset: 0, Count: int64(limit),
	}).Result()
	if err != nil {
		return nil, sourceError(ctx)
	}
	if len(entries) > limit {
		return nil, ErrInvalidSnapshot
	}

	taskIDs := make([]string, len(entries))
	aggregateBytes := 0
	for index, entry := range entries {
		taskID, ok := entry.Member.(string)
		if !ok || strings.TrimSpace(taskID) == "" || len(taskID) > maxTaskIDBytes ||
			!validReadyScore(entry.Score, nowMS) {
			return nil, ErrInvalidSnapshot
		}
		var withinBudget bool
		aggregateBytes, withinBudget = consumeBytes(aggregateBytes, len(taskID), maxAggregateBytes)
		if !withinBudget {
			return nil, ErrInvalidSnapshot
		}
		taskIDs[index] = taskID
	}
	if len(taskIDs) == 0 {
		return []Reference{}, nil
	}

	pipe := s.client.Pipeline()
	revisions := make([]*redis.StringCmd, len(taskIDs))
	for index, taskID := range taskIDs {
		revisions[index] = pipe.HGet(ctx, s.configsKey, taskID)
	}
	_, execErr := pipe.Exec(ctx)
	if execErr != nil && !errors.Is(execErr, redis.Nil) {
		return nil, sourceError(ctx)
	}

	references := make([]Reference, len(taskIDs))
	for index, command := range revisions {
		raw, resultErr := command.Result()
		if resultErr != nil {
			if errors.Is(resultErr, redis.Nil) {
				return nil, ErrInvalidSnapshot
			}
			return nil, sourceError(ctx)
		}
		if len(raw) == 0 || len(raw) > maxRevisionBytes {
			return nil, ErrInvalidSnapshot
		}
		var withinBudget bool
		aggregateBytes, withinBudget = consumeBytes(aggregateBytes, len(raw), maxAggregateBytes)
		if !withinBudget {
			return nil, ErrInvalidSnapshot
		}
		revision, parseErr := strconv.ParseInt(raw, 10, 64)
		if parseErr != nil || revision < 1 || revision > queuev2.RedisCandidateMaxInteger ||
			strconv.FormatInt(revision, 10) != raw {
			return nil, ErrInvalidSnapshot
		}
		references[index] = Reference{TaskID: taskIDs[index], ConfigRevision: revision}
	}
	return references, nil
}

func validateLimit(ctx context.Context, limit int) error {
	if ctx == nil || limit < 1 || limit > MaxPageSize {
		return ErrInvalidLimit
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	return nil
}

func validReadyScore(score float64, nowMS int64) bool {
	return !math.IsNaN(score) && !math.IsInf(score, 0) &&
		score >= 1 && score <= float64(nowMS) && score == math.Trunc(score) &&
		score <= float64(queuev2.RedisCandidateMaxInteger)
}

func consumeBytes(current, addition, maximum int) (int, bool) {
	if current < 0 || addition < 0 || maximum < 0 || current > maximum || addition > maximum-current {
		return current, false
	}
	return current + addition, true
}

func sourceError(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	return ErrSourceRead
}
