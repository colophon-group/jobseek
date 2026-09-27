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
	"reflect"
	"sort"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

// The shared enqueue script remains the queue/ready-set authority. Its
// embedded copy is checked byte-for-byte against the Python-owned source.
//
//go:embed deadletter_enqueue.lua
var deadletterEnqueueLua string

//go:embed deadletter_resolve.lua
var deadletterResolveLua string

//go:embed deadletter_delay.json
var deadletterDelayJSON []byte

type deadletterOptions struct {
	Action string
	Refs   []string
	Apply  bool
}
type deadletterRefs []string

func (v *deadletterRefs) String() string         { return strings.Join(*v, ",") }
func (v *deadletterRefs) Set(value string) error { *v = append(*v, value); return nil }
func parseDeadletterOptions(args []string) (deadletterOptions, error) {
	if len(args) == 0 {
		return deadletterOptions{}, errors.New("usage: --deadletters inspect|retry|prune [--entry REF] [--apply]")
	}
	option := deadletterOptions{Action: args[0]}
	if option.Action != "inspect" && option.Action != "retry" && option.Action != "prune" {
		return option, errors.New("deadletter action must be inspect, retry or prune")
	}
	set := flag.NewFlagSet("deadletters", flag.ContinueOnError)
	set.SetOutput(io.Discard)
	var refs deadletterRefs
	set.Var(&refs, "entry", "exact deadletter ref")
	set.BoolVar(&option.Apply, "apply", false, "apply exact selections")
	if err := set.Parse(args[1:]); err != nil || set.NArg() != 0 {
		return option, errors.New("invalid deadletter arguments")
	}
	option.Refs = refs
	if option.Action == "inspect" && option.Apply {
		return option, errors.New("inspect is always read-only; omit --apply")
	}
	if option.Action != "inspect" && len(refs) == 0 {
		return option, fmt.Errorf("%s requires at least one explicit --entry selector", option.Action)
	}
	return option, nil
}

func selectDeadletters(report deadletterReport, option deadletterOptions) (deadletterReport, error) {
	report.Action = option.Action
	report.DryRun = option.Action == "inspect" || !option.Apply
	if option.Action == "inspect" {
		return report, nil
	}
	byRef := map[string]deadletterEntry{}
	for _, entry := range report.Entries {
		byRef[entry.Ref] = entry
	}
	entries := []deadletterEntry{}
	seen := map[string]bool{}
	missing := []string{}
	for _, ref := range option.Refs {
		if seen[ref] {
			continue
		}
		seen[ref] = true
		entry, exists := byRef[ref]
		if !exists {
			missing = append(missing, ref)
			continue
		}
		entries = append(entries, entry)
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		return report, fmt.Errorf("selected dead-letter entries no longer exist: %s", strings.Join(missing, ", "))
	}
	for _, entry := range entries {
		if option.Action == "retry" && !entry.Retryable || option.Action == "prune" && !entry.Prunable {
			return report, fmt.Errorf("%s blocked by lifecycle guard: %s (%s)", option.Action, entry.Ref, entry.Reason)
		}
		// The legacy inspector's raw-key lookup treats noncanonical UUID strings
		// as removed. Never turn that historical classification into a mutation.
		canonical, ok := deadletterUUID(entry.TaskID)
		if !ok || canonical != entry.TaskID {
			return report, errors.New("noncanonical monitor ID requires manual review")
		}
	}
	report.Entries = entries
	report.Selected = len(entries)
	return report, nil
}

type deadletterDelays struct{ Default, ATS float64 }

func loadDeadletterDelays() (deadletterDelays, error) {
	delay := deadletterDelays{Default: 2, ATS: 0.5}
	for _, setting := range []struct {
		name  string
		value *float64
	}{{"THROTTLE_DELAY_DEFAULT", &delay.Default}, {"THROTTLE_DELAY_ATS", &delay.ATS}} {
		if raw := os.Getenv(setting.name); raw != "" {
			value, err := strconv.ParseFloat(raw, 64)
			if err != nil || math.IsNaN(value) || math.IsInf(value, 0) || value < 0 {
				return delay, fmt.Errorf("invalid %s", setting.name)
			}
			*setting.value = value
		}
	}
	return delay, nil
}
func (d deadletterDelays) forDomain(domain string) float64 {
	var contract struct {
		Domains  []string `json:"_KNOWN_ATS_DOMAINS"`
		Suffixes []string `json:"_KNOWN_ATS_DOMAIN_SUFFIXES"`
	}
	if err := json.Unmarshal(deadletterDelayJSON, &contract); err != nil {
		panic("invalid embedded domain-delay contract")
	}
	for _, candidate := range contract.Domains {
		if domain == candidate {
			return d.ATS
		}
	}
	for _, suffix := range contract.Suffixes {
		if strings.HasSuffix(domain, suffix) {
			return d.ATS
		}
	}
	return d.Default
}

type deadletterMutation struct {
	Wtype          string            `json:"wtype"`
	Member         string            `json:"member"`
	ReapedAt       float64           `json:"reaped_at"`
	TaskID         string            `json:"task_id"`
	Domain         string            `json:"domain"`
	NeedsSchedule  bool              `json:"needs_schedule"`
	Superseded     bool              `json:"superseded"`
	ExpectedWtype  string            `json:"expected_wtype"`
	ExpectedDomain string            `json:"expected_domain"`
	Config         map[string]string `json:"config"`
	Delay          string            `json:"delay"`
}

func mutateDeadletter(ctx context.Context, client *redis.Client, entry deadletterEntry, config map[string]string, action string, delays deadletterDelays) (string, error) {
	payload := deadletterMutation{Wtype: entry.Wtype, Member: entry.Member, ReapedAt: entry.ReapedAt, TaskID: entry.TaskID, Domain: entry.Domain, NeedsSchedule: action == "retry" || entry.Lifecycle == "superseded", Superseded: entry.Lifecycle == "superseded", Config: config}
	if payload.NeedsSchedule {
		if entry.ExpectedDomain == nil || entry.ExpectedWtype == nil {
			return "", errors.New("deadletter current route is unavailable")
		}
		payload.ExpectedDomain = *entry.ExpectedDomain
		payload.ExpectedWtype = *entry.ExpectedWtype
		delay := delays.forDomain(payload.ExpectedDomain)
		format := byte('f')
		if delay != 0 && (math.Abs(delay) < 1e-4 || math.Abs(delay) >= 1e16) {
			format = 'e'
		}
		payload.Delay = strconv.FormatFloat(delay, format, -1, 64)
		if !strings.ContainsAny(payload.Delay, ".e") {
			payload.Delay += ".0"
		}
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return "", errors.New("encode deadletter mutation failed")
	}
	script := "local function enqueue(ARGV)\n" + deadletterEnqueueLua + "\nend\n" + deadletterResolveLua
	result, err := client.Eval(ctx, script, []string{}, string(encoded)).Text()
	if err != nil {
		return "", fmt.Errorf("deadletter mutation was not acknowledged; inspect before retrying: %s", entry.Ref)
	}
	if result != "enqueued" && result != "already_scheduled" && result != "not_applicable" {
		return "", errors.New("invalid deadletter mutation acknowledgement; inspect before retrying")
	}
	return result, nil
}

func recoverDeadletter(ctx context.Context, pool *pgxpool.Pool, client *redis.Client, snapshot deadletterSnapshot, entry deadletterEntry, action string, delays deadletterDelays) (string, error) {
	tx, err := pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return "", errors.New("open deadletter recovery authority failed")
	}
	defer tx.Rollback(context.Background())
	// Keep existing board rows stable while the exact Redis transition executes.
	// A removed UUID has no row to lock; recovery never creates board authority.
	rows, err := tx.Query(ctx, deadletterBoardSQL+" FOR SHARE", []string{entry.TaskID})
	if err != nil {
		return "", errors.New("lock deadletter board authority failed")
	}
	current := deadletterSnapshot{Entries: []deadletterEntry{entry}, Boards: []deadletterBoard{}, Configs: map[string]map[string]string{}}
	for rows.Next() {
		var board deadletterBoard
		if err := rows.Scan(&board.ID, &board.Slug, &board.URL, &board.Crawler, &board.Status, &board.Enabled, &board.Domain, &board.Browser); err != nil {
			rows.Close()
			return "", errors.New("invalid deadletter recovery authority")
		}
		current.Boards = append(current.Boards, board)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return "", errors.New("read deadletter recovery authority failed")
	}
	expectedBoards := []deadletterBoard{}
	for _, board := range snapshot.Boards {
		if board.ID == entry.TaskID {
			expectedBoards = append(expectedBoards, board)
		}
	}
	if !reflect.DeepEqual(expectedBoards, current.Boards) {
		return "", errors.New("deadletter board authority changed; inspect again")
	}
	if entry.Retryable || entry.Lifecycle == "superseded" {
		config, err := client.HGetAll(ctx, "board:"+entry.TaskID).Result()
		if err != nil {
			return "", errors.New("read deadletter recovery config failed")
		}
		if !reflect.DeepEqual(config, snapshot.Configs[entry.TaskID]) {
			return "", errors.New("deadletter config changed; inspect again")
		}
		current.Configs[entry.TaskID] = config
	} else {
		current.Configs[entry.TaskID] = snapshot.Configs[entry.TaskID]
	}
	fresh, err := classifyDeadletterSnapshot(current)
	if err != nil {
		return "", err
	}
	if len(fresh.Entries) != 1 || !reflect.DeepEqual(fresh.Entries[0], entry) {
		return "", errors.New("deadletter lifecycle changed; inspect again")
	}
	outcome, err := mutateDeadletter(ctx, client, entry, current.Configs[entry.TaskID], action, delays)
	if err != nil {
		return "", err
	}
	// No PostgreSQL writes are performed. Rollback releases the shared row lock
	// even if Redis's acknowledgement is lost; it cannot undo an applied script.
	return outcome, nil
}

func resolveDeadletters(ctx context.Context, pool *pgxpool.Pool, client *redis.Client, option deadletterOptions, delays deadletterDelays) (deadletterReport, error) {
	snapshot, err := readDeadletterSnapshot(ctx, pool, client)
	if err != nil {
		return deadletterReport{}, err
	}
	report, err := classifyDeadletterSnapshot(snapshot)
	if err != nil {
		return report, err
	}
	report, err = selectDeadletters(report, option)
	if err != nil {
		return report, err
	}
	if option.Action == "inspect" {
		return report, nil
	}
	for _, entry := range report.Entries {
		if !option.Apply {
			report.Outcomes = append(report.Outcomes, map[string]string{"ref": entry.Ref, "outcome": "would_" + option.Action})
			continue
		}
		schedule, err := recoverDeadletter(ctx, pool, client, snapshot, entry, option.Action, delays)
		if err != nil {
			return report, err
		}
		outcome := "pruned"
		if option.Action == "retry" {
			outcome = "retried"
		}
		report.Outcomes = append(report.Outcomes, map[string]string{"ref": entry.Ref, "outcome": outcome, "schedule": schedule})
	}
	return report, nil
}
