// Package queue preserves the existing ordinary worker Redis/Lua ABI.
// ClaimFenced adds attempt tokens to the same queues and settlement scripts.
// These Redis tokens do not establish database or exclusive profile ownership;
// both must be complete before selecting native ordinary execution.
package queue

import (
	"context"
	"crypto/rand"
	_ "embed"
	"encoding/hex"
	"errors"
	"math"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/redis/go-redis/v9"
)

// The copies are checked byte-for-byte against repository Lua.
//
//go:embed claim_work.lua
var claimLua string

//go:embed heartbeat_task.lua
var heartbeatLua string

//go:embed complete_task.lua
var completeLua string

//go:embed reschedule_task.lua
var rescheduleLua string

var (
	ErrConfiguration = errors.New("ordinary queue configuration is invalid")
	ErrObservation   = errors.New("ordinary queue transition was not acknowledged")
	ErrProtocol      = errors.New("ordinary queue response is invalid")
)

type WorkerType string

const (
	Simple  WorkerType = "simple"
	Browser WorkerType = "browser"
)

type Kind string

const (
	Monitor Kind = "monitor"
	Scrape  Kind = "scrape"
)

type Settings struct {
	DefaultDelaySeconds float64
	LeaseTTL            time.Duration
	MaxDomains          int
}

type Task struct {
	Worker            WorkerType
	Kind              Kind
	ID                string
	Domain            string
	InitialLeaseUntil float64
	Config            map[string]string
	claimToken        string
}

type Client struct {
	redis                                  *redis.Client
	settings                               Settings
	claim, heartbeat, complete, reschedule *redis.Script
}

func validWorker(worker WorkerType) bool { return worker == Simple || worker == Browser }
func validPart(value string) bool {
	return value != "" && utf8.ValidString(value) && !strings.ContainsRune(value, '|') && !strings.ContainsFunc(value, unicode.IsControl)
}
func validTask(task *Task) bool {
	return task != nil && validWorker(task.Worker) && (task.Kind == Monitor || task.Kind == Scrape) && validPart(task.ID) && validPart(task.Domain) && (task.claimToken == "" || validToken(task.claimToken))
}
func validToken(token string) bool {
	if len(token) != 32 {
		return false
	}
	for _, ch := range token {
		if !(ch >= '0' && ch <= '9' || ch >= 'a' && ch <= 'f') {
			return false
		}
	}
	return true
}

// Fenced reports the descriptor's claim mode, not current Redis/DB authority.
func (task *Task) Fenced() bool { return task != nil && validToken(task.claimToken) }

func validTime(value float64) bool { return !math.IsNaN(value) && !math.IsInf(value, 0) && value >= 0 }

// Open owns its connection pool. Mutation retries are disabled: an ambiguous
// claim/settlement is left to the existing durable inflight/reaper protocol.
func Open(rawURL string, settings Settings) (*Client, error) {
	if settings.LeaseTTL <= 0 || settings.MaxDomains < 1 || !validTime(settings.DefaultDelaySeconds) {
		return nil, ErrConfiguration
	}
	options, err := redis.ParseURL(rawURL)
	if err != nil {
		return nil, ErrConfiguration
	}
	options.Protocol = 2
	options.MaxRetries = -1
	options.DialTimeout = 3 * time.Second
	options.ReadTimeout = 3 * time.Second
	options.WriteTimeout = 3 * time.Second
	options.PoolTimeout = 3 * time.Second
	options.ContextTimeoutEnabled = true
	options.PoolSize = 2
	return &Client{redis: redis.NewClient(options), settings: settings, claim: redis.NewScript(claimLua), heartbeat: redis.NewScript(heartbeatLua), complete: redis.NewScript(completeLua), reschedule: redis.NewScript(rescheduleLua)}, nil
}
func (c *Client) Close() error        { return c.redis.Close() }
func seconds(value time.Time) float64 { return float64(value.Unix()) + float64(value.Nanosecond())/1e9 }
func number(value float64) string     { return strconv.FormatFloat(value, 'f', -1, 64) }
func (c *Client) clock(ctx context.Context) (float64, error) {
	at, err := c.redis.Time(ctx).Result()
	if err != nil {
		return 0, ErrObservation
	}
	return seconds(at), nil
}

// Claim returns the descriptor even if the subsequent configuration read fails,
// so the caller retains its known inflight task for cleanup. Missing config is
// represented by an empty map, preserving the legacy reaper's orphan handling.
func (c *Client) Claim(ctx context.Context, worker WorkerType) (*Task, error) {
	return c.claimTask(ctx, worker, "")
}

// ClaimFenced creates a distinct attempt token. Expired/reaped/new attempts
// reject its heartbeat and settlement; the token stays private to this library.
// This does not admit a profile or authorize a PostgreSQL transaction.
func (c *Client) ClaimFenced(ctx context.Context, worker WorkerType) (*Task, error) {
	if !validWorker(worker) {
		return nil, ErrConfiguration
	}
	var token [16]byte
	if _, err := rand.Read(token[:]); err != nil {
		return nil, ErrObservation
	}
	return c.claimTask(ctx, worker, hex.EncodeToString(token[:]))
}

func (c *Client) claimTask(ctx context.Context, worker WorkerType, token string) (*Task, error) {
	return c.claimTaskBound(ctx, worker, token, nil)
}

func (c *Client) claimTaskBound(ctx context.Context, worker WorkerType, token string, binding []any) (*Task, error) {
	if !validWorker(worker) {
		return nil, ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	now, err := c.clock(ctx)
	if err != nil {
		return nil, err
	}
	args := []any{string(worker), number(now), number(c.settings.DefaultDelaySeconds), c.settings.MaxDomains, number(c.settings.LeaseTTL.Seconds()), token}
	args = append(args, binding...)
	raw, err := c.claim.Run(ctx, c.redis, nil, args...).Result()
	if errors.Is(err, redis.Nil) || (err == nil && raw == nil) {
		return nil, nil
	}
	if err != nil {
		return nil, ErrObservation
	}
	values, ok := raw.([]any)
	expected := 3
	if token != "" {
		expected = 5
	}
	if !ok || len(values) != expected {
		return nil, ErrProtocol
	}
	id, idOK := values[0].(string)
	kind, kindOK := values[1].(string)
	domain, domainOK := values[2].(string)
	task := &Task{Worker: worker, Kind: Kind(kind), ID: id, Domain: domain, InitialLeaseUntil: now + c.settings.LeaseTTL.Seconds()}
	if !idOK || !kindOK || !domainOK || !validTask(task) {
		return nil, ErrProtocol
	}
	if token != "" {
		echo, echoOK := values[3].(string)
		deadline, deadlineOK := values[4].(string)
		until, err := strconv.ParseFloat(deadline, 64)
		if !echoOK || echo != token || !deadlineOK || err != nil || !validTime(until) || until <= 0 {
			return task, ErrProtocol
		}
		task.claimToken = token
		task.InitialLeaseUntil = until
	}
	prefix := "scrape:"
	if task.Kind == Monitor {
		prefix = "board:"
	}
	task.Config, err = c.redis.HGetAll(ctx, prefix+task.ID).Result()
	if err != nil {
		return task, ErrObservation
	}
	for key, value := range task.Config {
		if !utf8.ValidString(key) || !utf8.ValidString(value) {
			return task, ErrProtocol
		}
	}
	return task, nil
}

// Heartbeat reports the existing Lua CH result. False can mean an unchanged
// deadline as well as a missing lease for legacy claims. For ClaimFenced, true
// means the same token was unexpired in the atomic operation; it grants no DB
// write authority and cannot substitute for the future transactional fence.
func (c *Client) Heartbeat(ctx context.Context, task *Task) (bool, error) {
	if !validTask(task) {
		return false, ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	now, err := c.clock(ctx)
	if err != nil {
		return false, err
	}
	return c.transition(ctx, c.heartbeat, string(task.Worker), string(task.Kind), task.Domain, task.ID, number(now+c.settings.LeaseTTL.Seconds()), task.claimToken)
}
func (c *Client) Complete(ctx context.Context, task *Task) (bool, error) {
	if !validTask(task) {
		return false, ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	return c.transition(ctx, c.complete, string(task.Worker), string(task.Kind), task.Domain, task.ID, task.claimToken)
}
func (c *Client) Reschedule(ctx context.Context, task *Task, nextDue float64) (bool, error) {
	return c.rescheduleHost(ctx, task, nextDue, nil)
}

func (c *Client) rescheduleHost(ctx context.Context, task *Task, nextDue float64, learned *string) (bool, error) {
	if !validTask(task) || !validTime(nextDue) {
		return false, ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	host := ""
	if learned != nil {
		if task.Kind != Monitor || task.Worker != Simple || !task.Fenced() || !validHost(*learned) || normalizeHost(*learned) != *learned {
			return false, ErrConfiguration
		}
		host = *learned
	}
	return c.transition(ctx, c.reschedule, string(task.Worker), task.Domain, task.ID, string(task.Kind), number(nextDue), task.claimToken, host)
}
func (c *Client) transition(ctx context.Context, script *redis.Script, args ...any) (bool, error) {
	raw, err := script.Run(ctx, c.redis, nil, args...).Result()
	if err != nil {
		return false, ErrObservation
	}
	value, ok := raw.(int64)
	if !ok || (value != 0 && value != 1) {
		return false, ErrProtocol
	}
	return value == 1, nil
}
