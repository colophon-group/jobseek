package queue

import (
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math/big"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"

	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
	"golang.org/x/net/idna"
)

//go:embed cold_b0_rollback.lua
var coldB0RollbackLua string

// ColdB0RollbackRequest names the exact retained retirement and explicit source
// queue. It does not adopt Redis's route or the PostgreSQL allocator. Receipt
// bytes and host/sentinel/release identities must be independently verified by
// the supported host wrapper before any later restoration mutation.
type ColdB0RollbackRequest struct {
	ReversalSHA256      string `json:"reversal_sha256"`
	SourceRevision      string `json:"source_revision"`
	RetirementEpoch     int64  `json:"retirement_epoch"`
	B0SourceEpoch       int64  `json:"b0_source_epoch"`
	SourceReceiptSHA256 string `json:"source_receipt_sha256"`
}

type coldB0RollbackEntry struct {
	Action     string            `json:"action"`
	Domain     string            `json:"domain,omitempty"`
	WorkerType string            `json:"worker_type,omitempty"`
	FirstTime  *bool             `json:"first_time,omitempty"`
	Score      string            `json:"score,omitempty"`
	Config     map[string]string `json:"config,omitempty"`
}

type coldB0RollbackDocument struct {
	Version        string                         `json:"version"`
	Request        ColdB0RollbackRequest          `json:"request"`
	TargetSHA256   string                         `json:"target_sha256"`
	Namespace      string                         `json:"namespace"`
	ShardID        string                         `json:"shard_id"`
	Cohort         string                         `json:"cohort"`
	SnapshotSHA256 string                         `json:"snapshot_sha256"`
	FenceTaskIDs   []string                       `json:"fence_task_ids"`
	RedisPlan      map[string]coldB0RollbackEntry `json:"redis_plan"`
}

// ColdB0RollbackPlan is an opaque observation for later durable approval. It
// neither journals a restoration nor grants permission to mutate/start owners.
type ColdB0RollbackPlan struct {
	document     coldB0RollbackDocument
	body, digest string
}

func (p *ColdB0RollbackPlan) SHA256() string  { return p.digest }
func (p *ColdB0RollbackPlan) Payload() string { return p.body }

type coldB0RollbackSnapshot struct {
	Records   map[string]string `json:"records"`
	Guards    map[string]string `json:"guards"`
	Authority [][]string        `json:"legacy_authority"`
}

func coldRollbackPairs(raw any) (map[string]string, error) {
	values, ok := raw.([]any)
	if !ok || len(values)%2 != 0 || len(values) > 4096 {
		return nil, ErrAuthorityLost
	}
	result := map[string]string{}
	for i := 0; i < len(values); i += 2 {
		key, kok := values[i].(string)
		value, vok := values[i+1].(string)
		if !kok || !vok {
			return nil, ErrAuthorityLost
		}
		if _, exists := result[key]; exists {
			return nil, ErrAuthorityLost
		}
		result[key] = value
	}
	return result, nil
}

func observeColdB0Rollback(ctx context.Context, c *Client, target *ColdB0Target, epoch int64) (*coldB0RollbackSnapshot, string, error) {
	script := "local function audited_b0()\n" + target.lua + "\nend\n" + coldB0RollbackLua
	raw, err := c.redis.Eval(ctx, script, target.keys(), target.auditArguments(epoch)...).Slice()
	if err != nil {
		var rejected redis.Error
		if errors.As(err, &rejected) {
			return nil, "", ErrAuthorityLost
		}
		return nil, "", ErrObservation
	}
	if len(raw) != 3 {
		return nil, "", ErrProtocol
	}
	records, err := coldRollbackPairs(raw[0])
	if err != nil {
		return nil, "", err
	}
	guards, err := coldRollbackPairs(raw[1])
	if err != nil || len(records) != len(guards) {
		return nil, "", ErrAuthorityLost
	}
	authority, ok := raw[2].([]any)
	if !ok || len(authority) != 4 {
		return nil, "", ErrProtocol
	}
	snapshot := &coldB0RollbackSnapshot{Records: records, Guards: guards, Authority: make([][]string, 4)}
	for i, value := range authority {
		members, ok := value.([]any)
		if !ok || len(members) > 65536 {
			return nil, "", ErrAuthorityLost
		}
		snapshot.Authority[i] = make([]string, len(members))
		for j, member := range members {
			text, ok := member.(string)
			if !ok || len(text) > 1024 {
				return nil, "", ErrAuthorityLost
			}
			snapshot.Authority[i][j] = text
		}
		sort.Strings(snapshot.Authority[i])
	}
	body, err := json.Marshal(snapshot)
	if err != nil {
		return nil, "", ErrProtocol
	}
	h := sha256.Sum256(body)
	return snapshot, hex.EncodeToString(h[:]), nil
}

type coldB0RollbackRecord struct {
	TaskID        string `json:"task_id"`
	State         string `json:"state"`
	Payload       string `json:"payload"`
	PayloadSHA256 string `json:"payload_sha256"`
}

var coldRollbackDomain = regexp.MustCompile(`^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$`)
var coldRollbackScore = regexp.MustCompile(`^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$`)

func coldRollbackSourceDomain(raw string) (string, error) {
	if len(raw) < 1 || len(raw) > 16384 {
		return "", ErrAuthorityLost
	}
	for _, r := range raw {
		if unicode.IsSpace(r) || r < 32 || r == 127 {
			return "", ErrAuthorityLost
		}
	}
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.Fragment != "" || (u.Port() != "" && u.Port() != "443") {
		return "", ErrAuthorityLost
	}
	domain, err := idna.Lookup.ToASCII(strings.ToLower(u.Hostname()))
	if err != nil || len(domain) > 253 || !coldRollbackDomain.MatchString(domain) {
		return "", ErrAuthorityLost
	}
	return domain, nil
}

func coldRollbackGuard(raw string, target *ColdB0Target, task b0task.Task) (string, string, error) {
	parts := strings.Split(raw, "|")
	e := task.Envelope
	if len(parts) != 7 || parts[0] != target.document.Namespace || parts[1] != e.ShardID || parts[2] != strconv.FormatInt(e.RoutingEpoch, 10) || parts[3] != e.BoardID || parts[4] != e.Domain || len(parts[6]) > 32 || !coldRollbackScore.MatchString(parts[6]) {
		return "", "", ErrAuthorityLost
	}
	switch parts[5] {
	case "ft_browser", "ft_simple", "recurring_browser", "recurring_simple":
	default:
		return "", "", ErrAuthorityLost
	}
	n, ok := new(big.Rat).SetString(parts[6])
	maximum := new(big.Rat).SetFrac64(9999999999999, 1000)
	if !ok || n.Sign() < 0 || n.Cmp(maximum) > 0 {
		return "", "", ErrAuthorityLost
	}
	f, err := strconv.ParseFloat(parts[6], 64)
	if err != nil || f == 0 && n.Sign() != 0 {
		return "", "", ErrAuthorityLost
	}
	return parts[5], parts[6], nil
}

// BuildColdB0RollbackPlan derives every schedule/config/drop from current
// PostgreSQL, retaining ready records' original transfer intent and exact score.
// Disabled/deleted/inactive work drops; dead/terminal work uses canonical due and
// current parser lane. Live leases, corrupt queues, suffix authority, drift or
// stale retirement reject before any effect. A second atomic observation binds
// the same queue snapshot. No HTTP/parser/database callback is replayed.
func BuildColdB0RollbackPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, request ColdB0RollbackRequest, target *ColdB0Target) (*ColdB0RollbackPlan, error) {
	if c == nil || target == nil || !ownershipSHA256.MatchString(request.ReversalSHA256) || !ownershipRevision.MatchString(request.SourceRevision) || !ownershipSHA256.MatchString(request.SourceReceiptSHA256) || request.RetirementEpoch < 2 || request.RetirementEpoch > 9999999999999 || request.B0SourceEpoch < 1 || request.B0SourceEpoch >= request.RetirementEpoch {
		return nil, ErrConfiguration
	}
	var result *ColdB0RollbackPlan
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		state, err := loadColdReversal(ctx, tx, request.ReversalSHA256, request.SourceRevision)
		if err != nil {
			return err
		}
		if state.phase != "reserved" || state.retirement != request.RetirementEpoch {
			return ErrAuthorityLost
		}
		if err := reversalForwardBinding(ctx, tx, state.spec, "reversing"); err != nil {
			return err
		}
		if err := reversalOwner(ctx, tx, state.spec, true); err != nil {
			return err
		}
		var current int64
		var called bool
		if err := tx.QueryRow(ctx, "SELECT last_value,is_called FROM public.lightpanda_b0_routing_epoch_seq").Scan(&current, &called); err != nil {
			return err
		}
		if !called || current != request.RetirementEpoch {
			return ErrAuthorityLost
		}
		var forwardBody string
		if err := tx.QueryRow(ctx, "SELECT payload FROM public.crawler_ownership_transition WHERE intent_sha256=$1", state.spec.ForwardIntentSHA256).Scan(&forwardBody); err != nil {
			return err
		}
		forward, err := decodeColdTransition(forwardBody, state.spec.ForwardIntentSHA256)
		if err != nil || forward.TargetB0ManifestSHA256 != target.digest {
			return ErrAuthorityLost
		}
		if request.B0SourceEpoch != state.spec.SourceEpoch {
			if request.B0SourceEpoch != forward.PreviousEpoch || (state.spec.SourcePhase != "reserved" && state.spec.SourcePhase != "publishing") {
				return ErrAuthorityLost
			}
		}
		snapshot, snapshotHash, err := observeColdB0Rollback(ctx, c, target, request.B0SourceEpoch)
		if err != nil {
			return err
		}
		ids := map[string]bool{}
		records := map[string]coldB0RollbackRecord{}
		tasks := map[string]b0task.Task{}
		for id, raw := range snapshot.Records {
			if !canonicalUUID.MatchString(id) {
				return ErrAuthorityLost
			}
			var record coldB0RollbackRecord
			decoder := json.NewDecoder(strings.NewReader(raw))
			decoder.UseNumber()
			value, parseErr := coldJSONValue(decoder, 0)
			object, isObject := value.(map[string]any)
			if parseErr != nil || !isObject || len(object) != 20 {
				return ErrAuthorityLost
			}
			if _, err := decoder.Token(); err != io.EOF {
				return ErrAuthorityLost
			}
			if json.Unmarshal([]byte(raw), &record) != nil || record.TaskID != id || (record.State != "ready" && record.State != "dead" && record.State != "terminal") {
				return ErrAuthorityLost
			}
			payloadDecoder := json.NewDecoder(strings.NewReader(record.Payload))
			payloadDecoder.UseNumber()
			if _, err := coldJSONValue(payloadDecoder, 0); err != nil {
				return ErrAuthorityLost
			}
			if _, err := payloadDecoder.Token(); err != io.EOF {
				return ErrAuthorityLost
			}
			task, err := b0task.Decode(record.Payload, record.PayloadSHA256, b0task.Route{ShardID: target.document.ShardID, RoutingEpoch: request.B0SourceEpoch, EngineOwner: "go"})
			if err != nil || task.Envelope.TaskID != id {
				return ErrAuthorityLost
			}
			allowed := false
			for _, board := range target.document.Boards {
				if board.ID == task.Envelope.BoardID {
					allowed = true
				}
			}
			if !allowed {
				return ErrAuthorityLost
			}
			ids[id] = true
			records[id] = record
			tasks[id] = task
		}
		fenceIDs := []string{}
		fences, err := tx.Query(ctx, `SELECT job_posting_id::text FROM public.lightpanda_b0_write_fence WHERE engine_owner='go' AND shard_id=$1 AND routing_epoch=$2 ORDER BY job_posting_id LIMIT 2049`, target.document.ShardID, request.B0SourceEpoch)
		if err != nil {
			return err
		}
		for fences.Next() {
			var id string
			if err := fences.Scan(&id); err != nil {
				fences.Close()
				return err
			}
			fenceIDs = append(fenceIDs, id)
			ids[id] = true
		}
		err = fences.Err()
		fences.Close()
		if err != nil {
			return err
		}
		if len(ids) > 2048 {
			return ErrAuthorityLost
		}
		for _, members := range snapshot.Authority {
			for _, member := range members {
				if strings.HasPrefix(member, "scrape|") {
					at := strings.LastIndexByte(member, '|')
					if at >= 0 && ids[member[at+1:]] {
						return ErrAuthorityLost
					}
				}
			}
		}
		queryIDs := make([]string, 0, len(ids))
		for id := range ids {
			queryIDs = append(queryIDs, id)
		}
		sort.Strings(queryIDs)
		plan := map[string]coldB0RollbackEntry{}
		for id := range records {
			plan[id] = coldB0RollbackEntry{Action: "drop"}
		}
		rows, err := tx.Query(ctx, `SELECT jp.id::text,jp.board_id::text,jp.source_url,jp.description_r2_hash,jp.is_active,jp.next_scrape_at,
 COALESCE(jp.leased_until>now(),false),jb.is_enabled,jb.board_status,jb.scraper_needs_browser,jb.scrape_interval_hours
 FROM public.job_posting jp JOIN public.job_board jb ON jb.id=jp.board_id WHERE jp.id=ANY($1::uuid[]) ORDER BY jp.id FOR SHARE OF jp,jb`, queryIDs)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var id, board, source, status string
			var hash *int64
			var active, leased, enabled, browser bool
			var due *time.Time
			var interval int
			if err := rows.Scan(&id, &board, &source, &hash, &active, &due, &leased, &enabled, &status, &browser, &interval); err != nil {
				return err
			}
			if leased {
				return ErrAuthorityLost
			}
			record, exists := records[id]
			if !exists || !active || due == nil || !enabled || status != "active" {
				continue
			}
			domain, err := coldRollbackSourceDomain(source)
			if err != nil || !canonicalUUID.MatchString(board) || interval < 1 || interval > 8760 {
				return ErrAuthorityLost
			}
			first := hash == nil
			worker := "simple"
			if browser {
				worker = "browser"
			}
			score := "0"
			if !first {
				seconds := due.Unix()
				micros := due.Nanosecond() / 1000
				if seconds < 0 || seconds > 9999999999 || seconds == 9999999999 && micros > 999000 {
					return ErrAuthorityLost
				}
				score = strconv.FormatInt(seconds, 10)
				if micros != 0 {
					score += "." + strings.TrimRight(strconv.FormatInt(int64(micros)+1000000, 10)[1:], "0")
				}
			}
			if record.State == "ready" {
				kind, guardScore, err := coldRollbackGuard(snapshot.Guards[id], target, tasks[id])
				if err != nil {
					return err
				}
				first = strings.HasPrefix(kind, "ft_")
				worker = strings.TrimPrefix(strings.TrimPrefix(kind, "ft_"), "recurring_")
				score = guardScore
			}
			hint := ""
			if hash != nil {
				hint = strconv.FormatInt(*hash, 10)
			}
			plan[id] = coldB0RollbackEntry{Action: "schedule", Domain: domain, WorkerType: worker, FirstTime: &first, Score: score, Config: map[string]string{"domain": domain, "board_id": board, "source_url": source, "description_r2_hash": hint, "scrape_step": "0", "scrape_interval_hours": strconv.Itoa(interval)}}
		}
		if err := rows.Err(); err != nil {
			return err
		}
		rows.Close()
		_, finalHash, err := observeColdB0Rollback(ctx, c, target, request.B0SourceEpoch)
		if err != nil {
			return err
		}
		if finalHash != snapshotHash {
			return ErrAuthorityLost
		}
		doc := coldB0RollbackDocument{Version: "jobseek.crawler.cold-b0-rollback/v1", Request: request, TargetSHA256: target.digest, Namespace: target.document.Namespace, ShardID: target.document.ShardID, Cohort: target.document.Cohort, SnapshotSHA256: snapshotHash, FenceTaskIDs: fenceIDs, RedisPlan: plan}
		body, err := json.Marshal(doc)
		if err != nil || len(body) > 32*1024*1024 {
			return ErrProtocol
		}
		digest := sha256.Sum256(body)
		result = &ColdB0RollbackPlan{document: doc, body: string(body), digest: hex.EncodeToString(digest[:])}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}
