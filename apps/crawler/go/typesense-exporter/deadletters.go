package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"os/signal"
	"sort"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

// This process is read-only. Poison descriptors remain parked until an
// explicitly selected retry/prune; normal sync never clears them.
type deadletterEntry struct {
	Member         string  `json:"member"`
	Wtype          string  `json:"wtype"`
	ReapedAt       float64 `json:"reaped_at"`
	TaskType       string  `json:"task_type"`
	Domain         string  `json:"domain"`
	TaskID         string  `json:"task_id"`
	Lifecycle      string  `json:"lifecycle"`
	Reason         string  `json:"reason"`
	Resolution     string  `json:"resolution"`
	BoardSlug      *string `json:"board_slug"`
	BoardStatus    *string `json:"board_status"`
	ExpectedDomain *string `json:"expected_domain"`
	ExpectedWtype  *string `json:"expected_wtype"`
	ConfigState    string  `json:"config_state"`
	Ref            string  `json:"ref"`
	ReapedAtISO    string  `json:"reaped_at_iso"`
	Retryable      bool    `json:"retryable"`
	Prunable       bool    `json:"prunable"`
}

type deadletterBoard struct {
	ID      string  `json:"board_id"`
	Slug    *string `json:"board_slug"`
	URL     *string `json:"board_url"`
	Crawler *string `json:"crawler_type"`
	Status  *string `json:"board_status"`
	Enabled bool    `json:"is_enabled"`
	Domain  *string `json:"throttle_key"`
	Browser bool    `json:"monitor_needs_browser"`
}

type deadletterSnapshot struct {
	Entries []deadletterEntry            `json:"entries"`
	Boards  []deadletterBoard            `json:"boards"`
	Configs map[string]map[string]string `json:"configs"`
}

type deadletterReport struct {
	Action   string                    `json:"action"`
	DryRun   bool                      `json:"dry_run"`
	Total    int                       `json:"total"`
	Counts   map[string]map[string]int `json:"counts"`
	Selected int                       `json:"selected"`
	Entries  []deadletterEntry         `json:"entries"`
	Outcomes []map[string]string       `json:"outcomes"`
}

// Python UUID(string) accepts compact, hyphenated, braced and urn forms.
// Keep the original descriptor intact: only the database bind is canonical.
func deadletterUUID(raw string) (string, bool) {
	value := strings.ReplaceAll(strings.ReplaceAll(raw, "urn:", ""), "uuid:", "")
	value = strings.Trim(value, "{}")
	value = strings.ReplaceAll(value, "-", "")
	if len(value) != 32 {
		return "", false
	}
	if _, err := hex.DecodeString(value); err != nil {
		return "", false
	}
	value = strings.ToLower(value)
	return value[:8] + "-" + value[8:12] + "-" + value[12:16] + "-" + value[16:20] + "-" + value[20:], true
}

func parseDeadletter(wtype, member string, score float64) (deadletterEntry, error) {
	e := deadletterEntry{Member: member, Wtype: wtype, ReapedAt: score, Lifecycle: "unresolved", Reason: "unclassified", Resolution: "manual_review", ConfigState: "not_applicable", Ref: wtype + ":" + member}
	if !utf8.ValidString(member) {
		return e, errors.New("invalid deadletter UTF-8")
	}
	if math.IsNaN(score) || math.IsInf(score, 0) || score < -62135596800 || score >= 253402300800 {
		return e, errors.New("invalid deadletter timestamp")
	}
	// datetime.fromtimestamp rounds fractional seconds to the nearest microsecond.
	sec, fraction := math.Modf(score)
	stamp := time.Unix(int64(sec), int64(math.RoundToEven(fraction*1e6))*1000).UTC()
	layout := "2006-01-02T15:04:05"
	if stamp.Nanosecond() != 0 {
		layout += ".000000"
	}
	e.ReapedAtISO = stamp.Format(layout) + "+00:00"
	parts := strings.SplitN(member, "|", 3)
	if len(parts) > 0 {
		e.TaskType = parts[0]
	}
	if len(parts) > 1 {
		e.Domain = parts[1]
	}
	if len(parts) > 2 {
		e.TaskID = parts[2]
	}
	switch {
	case len(parts) != 3 || e.TaskType == "" || e.Domain == "" || e.TaskID == "":
		e.Reason = "malformed_descriptor"
	case e.TaskType != "monitor":
		e.Reason = "non_monitor_task"
	default:
		if _, ok := deadletterUUID(e.TaskID); !ok {
			e.Reason = "invalid_monitor_id"
		}
	}
	return e, nil
}

func deadletterConfigState(config map[string]string, board deadletterBoard) string {
	if len(config) == 0 {
		return "missing"
	}
	equal := func(key string, value *string) bool {
		got, ok := config[key]
		if value == nil {
			return !ok
		}
		return ok && got == *value
	}
	domain := ""
	if board.Domain != nil {
		domain = *board.Domain
	}
	browser := strings.ToLower(strings.TrimSpace(config["monitor_needs_browser"]))
	isBrowser := browser == "1" || browser == "true" || browser == "yes"
	actualDomain, hasDomain := config["domain"]
	if !hasDomain || actualDomain != domain || !equal("board_url", board.URL) || !equal("crawler_type", board.Crawler) || isBrowser != board.Browser {
		return "stale"
	}
	return "valid"
}

func classifyDeadletterSnapshot(snapshot deadletterSnapshot) (deadletterReport, error) {
	report := deadletterReport{Action: "inspect", DryRun: true, Counts: map[string]map[string]int{}, Entries: []deadletterEntry{}, Outcomes: []map[string]string{}}
	for _, wtype := range []string{"simple", "browser"} {
		report.Counts[wtype] = map[string]int{"actionable": 0, "retired": 0, "superseded": 0, "unresolved": 0}
	}
	boards := map[string]deadletterBoard{}
	for _, board := range snapshot.Boards {
		boards[board.ID] = board
	}
	for _, raw := range snapshot.Entries {
		if raw.Wtype != "simple" && raw.Wtype != "browser" {
			return report, errors.New("invalid deadletter worker type")
		}
		e, err := parseDeadletter(raw.Wtype, raw.Member, raw.ReapedAt)
		if err != nil {
			return report, err
		}
		if e.Reason == "unclassified" {
			board, exists := boards[e.TaskID]
			if !exists {
				e.Lifecycle, e.Reason, e.Resolution = "retired", "removed", "prune"
			} else {
				domain := ""
				if board.Domain != nil {
					domain = *board.Domain
				}
				wtype := "simple"
				if board.Browser {
					wtype = "browser"
				}
				e.BoardSlug, e.BoardStatus, e.ExpectedDomain, e.ExpectedWtype = board.Slug, board.Status, &domain, &wtype
				e.ConfigState = deadletterConfigState(snapshot.Configs[e.TaskID], board)
				switch {
				case !board.Enabled || (board.Status != nil && *board.Status == "disabled"):
					e.Lifecycle, e.Reason, e.Resolution = "retired", "disabled", "prune"
				case e.Domain != domain || e.Wtype != wtype:
					if e.ConfigState == "valid" {
						e.Lifecycle, e.Reason, e.Resolution = "superseded", "monitor_route_changed", "prune_after_current_schedule"
					} else {
						e.Reason, e.Resolution = "superseded_config_"+e.ConfigState, "sync_then_inspect"
					}
				case e.ConfigState != "valid":
					e.Lifecycle, e.Reason, e.Resolution = "actionable", "active_config_"+e.ConfigState, "sync_then_retry"
				default:
					e.Lifecycle, e.Reason, e.Resolution = "actionable", "active", "retry"
				}
			}
		}
		e.Retryable = e.Lifecycle == "actionable" && e.Reason == "active"
		e.Prunable = e.Lifecycle == "retired" || e.Lifecycle == "superseded"
		report.Entries = append(report.Entries, e)
		report.Counts[e.Wtype][e.Lifecycle]++
	}
	sort.Slice(report.Entries, func(i, j int) bool {
		a, b := report.Entries[i], report.Entries[j]
		if a.Wtype != b.Wtype {
			return a.Wtype < b.Wtype
		}
		if a.ReapedAt != b.ReapedAt {
			return a.ReapedAt < b.ReapedAt
		}
		return a.Member < b.Member
	})
	report.Total = len(report.Entries)
	report.Selected = report.Total
	return report, nil
}

const deadletterBoardSQL = `SELECT id::text,board_slug,board_url,crawler_type,board_status,COALESCE(is_enabled,false),throttle_key,COALESCE(monitor_needs_browser,false) FROM job_board WHERE id=ANY($1::uuid[])`

func readDeadletterSnapshot(ctx context.Context, pool *pgxpool.Pool, client *redis.Client) (deadletterSnapshot, error) {
	snapshot := deadletterSnapshot{Entries: []deadletterEntry{}, Boards: []deadletterBoard{}, Configs: map[string]map[string]string{}}
	ids := map[string]bool{}
	// One ZRANGE per lane, as in the existing classifier: pagination could lose
	// members when the reaper adds or an operator removes entries mid-scan.
	for _, wtype := range []string{"simple", "browser"} {
		values, err := client.ZRangeWithScores(ctx, "deadletter:"+wtype, 0, -1).Result()
		if err != nil {
			return snapshot, errors.New("read deadletter lane failed")
		}
		for _, value := range values {
			member, ok := value.Member.(string)
			if !ok {
				return snapshot, errors.New("invalid deadletter member")
			}
			e, err := parseDeadletter(wtype, member, value.Score)
			if err != nil {
				return snapshot, err
			}
			snapshot.Entries = append(snapshot.Entries, e)
			if e.Reason == "unclassified" {
				id, _ := deadletterUUID(e.TaskID)
				ids[id] = true
			}
		}
	}
	if len(ids) == 0 {
		return snapshot, nil
	}
	ordered := make([]string, 0, len(ids))
	for id := range ids {
		ordered = append(ordered, id)
	}
	sort.Strings(ordered)
	tx, err := pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return snapshot, errors.New("open deadletter authority snapshot failed")
	}
	defer tx.Rollback(context.Background())
	for start := 0; start < len(ordered); start += 1000 {
		end := min(start+1000, len(ordered))
		rows, err := tx.Query(ctx, deadletterBoardSQL, ordered[start:end])
		if err != nil {
			return snapshot, errors.New("read deadletter board authority failed")
		}
		for rows.Next() {
			var board deadletterBoard
			if err := rows.Scan(&board.ID, &board.Slug, &board.URL, &board.Crawler, &board.Status, &board.Enabled, &board.Domain, &board.Browser); err != nil {
				rows.Close()
				return snapshot, errors.New("invalid deadletter board authority")
			}
			snapshot.Boards = append(snapshot.Boards, board)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return snapshot, errors.New("read deadletter board rows failed")
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return snapshot, errors.New("close deadletter authority snapshot failed")
	}
	for start := 0; start < len(snapshot.Boards); start += 1000 {
		end := min(start+1000, len(snapshot.Boards))
		pipe := client.Pipeline()
		commands := make([]*redis.MapStringStringCmd, 0, end-start)
		for _, board := range snapshot.Boards[start:end] {
			commands = append(commands, pipe.HGetAll(ctx, "board:"+board.ID))
		}
		if _, err := pipe.Exec(ctx); err != nil {
			return snapshot, errors.New("read deadletter board configs failed")
		}
		for i, command := range commands {
			for key, value := range command.Val() {
				if !utf8.ValidString(key) || !utf8.ValidString(value) {
					return snapshot, errors.New("invalid deadletter config UTF-8")
				}
			}
			snapshot.Configs[snapshot.Boards[start+i].ID] = command.Val()
		}
	}
	return snapshot, nil
}

func runDeadletters(args []string) error {
	option, err := parseDeadletterOptions(args)
	if err != nil {
		return err
	}
	delays, err := loadDeadletterDelays()
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, 45*time.Second)
	defer cancel()
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
	config.ConnConfig.RuntimeParams["application_name"] = "jobseek:crawler:deadletters|" + option.Action + ":local"
	config.ConnConfig.RuntimeParams["statement_timeout"] = "15000"
	config.ConnConfig.RuntimeParams["lock_timeout"] = "5000"
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return errors.New("open local database pool failed")
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
	options.DialTimeout = 5 * time.Second
	options.ReadTimeout = 10 * time.Second
	options.ContextTimeoutEnabled = true
	// A lost write acknowledgement must never be retried automatically.
	options.MaxRetries = -1
	client := redis.NewClient(options)
	defer client.Close()
	report, err := resolveDeadletters(ctx, pool, client, option, delays)
	if err != nil {
		return err
	}
	if err := json.NewEncoder(os.Stdout).Encode(report); err != nil {
		return fmt.Errorf("write deadletter report: %w", err)
	}
	return nil
}
