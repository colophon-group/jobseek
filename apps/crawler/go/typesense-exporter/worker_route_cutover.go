package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

const bajajRouteBoard = "bajaj-finserv-careers-ps-jobs"
const bajajRouteDomain = "bflcareers.peoplestrong.com"

var bajajRouteURL = regexp.MustCompile(`^https://bflcareers[.]peoplestrong[.]com/job/detail/JR[0-9]+$`)

//go:embed provider-cutover/worker-routes.lua
var workerRouteCutoverLua string

type workerRouteCutoverSummary struct {
	Selected int    `json:"selected"`
	Repaired int64  `json:"repaired"`
	Worker   string `json:"worker"`
}

// This is an explicit provider cutover, not a global queue rebuild. The whole
// deploy is quiesced; restored board state also selects the reverse direction.
func repairWorkerRouteCutover(ctx context.Context, conn *pgx.Conn, client *redis.Client) (workerRouteCutoverSummary, error) {
	var result workerRouteCutoverSummary
	var ownershipTable *string
	if err := conn.QueryRow(ctx, `SELECT to_regclass('public.ordinary_worker_ownership_plan')::text`).Scan(&ownershipTable); err != nil {
		return result, errors.New("worker route SQL ownership check failed")
	}
	if ownershipTable != nil {
		var active bool
		if err := conn.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active')`).Scan(&active); err != nil || active {
			return result, errors.New("worker route SQL ownership is still active or unavailable")
		}
	}
	var board, boardURL string
	var browser bool
	var metadata []byte
	err := conn.QueryRow(ctx, `SELECT id::text,board_url,scraper_needs_browser,metadata FROM job_board WHERE board_slug=$1`, bajajRouteBoard).Scan(&board, &boardURL, &browser, &metadata)
	if errors.Is(err, pgx.ErrNoRows) {
		return result, nil
	}
	if err != nil {
		return result, errors.New("worker route board query failed")
	}
	var config struct {
		Type   string         `json:"scraper_type"`
		Config map[string]any `json:"scraper_config"`
	}
	if json.Unmarshal(metadata, &config) != nil || boardURL != "https://bflcareers.peoplestrong.com/job/joblist" {
		return result, errors.New("worker route board identity mismatch")
	}
	result.Worker = "simple"
	if browser {
		result.Worker = "browser"
		if config.Type != "dom" || config.Config["render"] != true {
			return result, errors.New("unsupported rollback worker route")
		}
	} else if config.Type != "api_sniffer" || config.Config["api_url"] != "https://bflcareers.peoplestrong.com/api/cp/rest/altone/cp/job/{id}/v2?part=basic,organisational,descriprion,workflow,skill,qualification,certification,language,applied&isReqId=false" {
		return result, errors.New("unsupported forward worker route")
	}
	rows, err := conn.Query(ctx, `SELECT job_posting.id::text,source_url,board_id::text,description_r2_hash,next_scrape_at,scraper_needs_browser FROM job_posting JOIN job_board ON job_board.id=job_posting.board_id WHERE job_posting.board_id=$1::uuid AND job_posting.is_active AND next_scrape_at IS NOT NULL ORDER BY job_posting.id LIMIT 10001`, board)
	if err != nil {
		return result, errors.New("worker route schedule query failed")
	}
	schedules, err := pgx.CollectRows(rows, pgx.RowToStructByPos[capacitySchedule])
	if err != nil || len(schedules) > 10000 {
		return result, errors.New("worker route schedule inventory exceeds bound or is invalid")
	}
	result.Selected = len(schedules)
	args := []any{board, bajajRouteDomain, result.Worker, strconv.FormatFloat(float64(time.Now().UnixNano())/1e9, 'f', -1, 64)}
	for _, s := range schedules {
		domain, score, cfg, err := capacityScheduleValues(s)
		if err != nil || domain != bajajRouteDomain || !bajajRouteURL.MatchString(s.URL) || s.Browser != browser {
			return result, errors.New("worker route canonical posting identity mismatch")
		}
		args = append(args, s.ID, s.URL, cfg["description_r2_hash"], strconv.FormatFloat(score, 'f', -1, 64), strconv.FormatBool(s.Hash == nil))
	}
	// Wrap the unchanged original enqueue implementation so both ready indexes
	// retain their rate-limit and rotation semantics after lane normalization.
	readyBlock := strings.SplitN(deadletterEnqueueLua, "-- Always recompute ready membership.", 2)[1]
	readyBlock = strings.TrimSuffix(readyBlock, "return added\n")
	script := workerRouteCutoverLua + "\nlocal function enqueue(ARGV)\n" + deadletterEnqueueLua + "\nend\nlocal function refresh(wtype)\nlocal domain=ARGV[2]\nlocal now=tonumber(ARGV[4])\nlocal scrape_rotation_key=\"ready:rotation:\"..wtype\n-- Always recompute ready membership." + readyBlock + "\nend\nreturn repair(enqueue,refresh)\n"
	result.Repaired, err = client.Eval(ctx, script, nil, args...).Int64()
	if err != nil || result.Repaired < 0 || result.Repaired > int64(result.Selected) {
		return result, errors.New("worker route repair was not acknowledged")
	}
	return result, nil
}
