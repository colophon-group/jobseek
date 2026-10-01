package queue

import (
	"bytes"
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"math/big"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

//go:embed cold_b0_forward.lua
var coldB0ForwardLua string

// The exact retained reservation names all source/target releases and host cold
// attestation. Neither this request nor the producer socket attests the host.
type ColdB0ForwardRequest struct {
	IntentSHA256       string `json:"intent_sha256"`
	SourceRevision     string `json:"source_revision"`
	RoutingEpoch       int64  `json:"routing_epoch"`
	OrdinaryPlanSHA256 string `json:"ordinary_plan_sha256"`
}

func (r ColdB0ForwardRequest) valid() bool {
	return ownershipSHA256.MatchString(r.IntentSHA256) && ownershipRevision.MatchString(r.SourceRevision) && ownershipSHA256.MatchString(r.OrdinaryPlanSHA256) && r.RoutingEpoch > 1 && r.RoutingEpoch <= 9999999999999
}

func DecodeColdB0ForwardRequest(body, digest string) (ColdB0ForwardRequest, error) {
	var r ColdB0ForwardRequest
	h := sha256.Sum256([]byte(body))
	if len(body) < 1 || len(body) > 4096 || !ownershipSHA256.MatchString(digest) || hex.EncodeToString(h[:]) != digest {
		return r, ErrConfiguration
	}
	d := json.NewDecoder(strings.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(&r) != nil || d.Decode(new(any)) != io.EOF || !r.valid() {
		return ColdB0ForwardRequest{}, ErrConfiguration
	}
	wanted, err := json.Marshal(r)
	if err != nil || !bytes.Equal(wanted, []byte(body)) {
		return ColdB0ForwardRequest{}, ErrConfiguration
	}
	return r, nil
}

type coldB0ForwardTask struct {
	PostingID             string            `json:"posting_id"`
	Domain                string            `json:"domain"`
	NextScrapeAtMS        int64             `json:"next_scrape_at_ms"`
	FirstTime             bool              `json:"first_time"`
	Config                map[string]string `json:"config"`
	LegacyScheduleScore   string            `json:"legacy_schedule_score"`
	PostgresHash          string            `json:"postgres_description_r2_hash"`
	PreparationDigest     string            `json:"preparation_digest"`
	PayloadSHA256         string            `json:"payload_sha256"`
	ExistingState         string            `json:"existing_state"`
	ExistingPayloadSHA256 string            `json:"existing_payload_sha256"`
}

func (t coldB0ForwardTask) task() b0producer.Task {
	return b0producer.Task{PostingID: t.PostingID, Domain: t.Domain, NextScrapeAtMS: t.NextScrapeAtMS, Config: t.Config, Browser: true, FirstTime: t.FirstTime, LegacyScheduleScore: t.LegacyScheduleScore}
}

type coldB0ForwardDocument struct {
	Version             string                 `json:"version"`
	Request             ColdB0ForwardRequest   `json:"request"`
	TargetSHA256        string                 `json:"target_sha256"`
	PostgresSHA256      string                 `json:"postgres_sha256"`
	SnapshotSHA256      string                 `json:"snapshot_sha256"`
	Target              coldB0Document         `json:"target"`
	PostgresRows        []coldB0ForwardRow     `json:"postgres_rows"`
	Snapshot            *coldB0ForwardSnapshot `json:"snapshot"`
	Tasks               []coldB0ForwardTask    `json:"tasks"`
	UnqueuedPostingIDs  []string               `json:"unqueued_posting_ids"`
	RetainedTerminalIDs []string               `json:"retained_terminal_ids"`
	LifetimeOccupancy   int64                  `json:"lifetime_occupancy"`
	LifetimeCapacity    int64                  `json:"lifetime_capacity"`
	NewRecordCount      int64                  `json:"new_record_count"`
	ProjectedOccupancy  int64                  `json:"projected_lifetime_occupancy"`
}

type ColdB0ForwardPlan struct {
	document     coldB0ForwardDocument
	body, digest string
}

func (p *ColdB0ForwardPlan) SHA256() string                { return p.digest }
func (p *ColdB0ForwardPlan) Payload() string               { return p.body }
func (p *ColdB0ForwardPlan) Request() ColdB0ForwardRequest { return p.document.Request }

// Private interface allows bounded pure preparation fixtures. Exported runtime
// entry points require the fixed authenticated native Client, with no override.
type coldB0ForwardControl interface {
	Manifest(context.Context, string) (b0producer.Response, error)
	Prepare(context.Context, b0producer.Task) (b0producer.Response, error)
}

type coldB0ForwardRow struct {
	ID        string `json:"posting_id"`
	BoardID   string `json:"board_id"`
	SourceURL string `json:"source_url"`
	Domain    string `json:"domain"`
	Hash      string `json:"description_r2_hash"`
	DueScore  string `json:"due_score"`
	Interval  int    `json:"interval_hours"`
}

type coldB0ForwardLegacy struct {
	Config map[string]string `json:"config"`
	Scores []string          `json:"scores"`
}
type coldB0ForwardSnapshot struct {
	B0     *coldB0RollbackSnapshot `json:"b0"`
	Legacy []coldB0ForwardLegacy   `json:"legacy"`
}

func coldB0ForwardContext(ctx context.Context, tx pgx.Tx, c *Client, r ColdB0ForwardRequest, target *ColdB0Target) (*coldPublication, error) {
	p, err := loadColdPublication(ctx, tx, c, r.IntentSHA256, r.SourceRevision, target)
	if err != nil {
		return nil, err
	}
	if p.phase != "reserved" || p.plan.Epoch() != r.RoutingEpoch || p.plan.digest != r.OrdinaryPlanSHA256 {
		return nil, ErrAuthorityLost
	}
	var active bool
	if err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ordinary_worker_ownership_plan WHERE state='active')").Scan(&active); err != nil {
		return nil, err
	}
	if active {
		return nil, ErrAuthorityLost
	}
	return p, nil
}

func coldB0DueScore(due time.Time) (string, error) {
	seconds, micros := due.Unix(), due.Nanosecond()/1000
	if seconds < 0 || seconds > 9999999999 || seconds == 9999999999 && micros > 999000 || due.Nanosecond()%1000 != 0 {
		return "", ErrAuthorityLost
	}
	s := strconv.FormatInt(seconds, 10)
	if micros != 0 {
		s += "." + strings.TrimRight(strconv.Itoa(micros + 1000000)[1:], "0")
	}
	return s, nil
}

func coldB0ForwardRows(ctx context.Context, tx pgx.Tx, target *ColdB0Target) ([]coldB0ForwardRow, string, error) {
	boards := []string{}
	for _, b := range target.document.Boards {
		boards = append(boards, b.ID)
	}
	var leased bool
	if err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM job_posting WHERE board_id=ANY($1::uuid[]) AND leased_until>now())", boards).Scan(&leased); err != nil {
		return nil, "", err
	}
	if leased {
		return nil, "", ErrAuthorityLost
	}
	rows, err := tx.Query(ctx, `SELECT jp.id::text,jp.board_id::text,jp.source_url,jp.description_r2_hash,jp.next_scrape_at,jb.scrape_interval_hours
 FROM job_posting jp JOIN job_board jb ON jb.id=jp.board_id
 WHERE jp.board_id=ANY($1::uuid[]) AND jp.is_active AND jp.next_scrape_at IS NOT NULL
 ORDER BY jp.id LIMIT 2049 FOR SHARE OF jp,jb`, boards)
	if err != nil {
		return nil, "", err
	}
	defer rows.Close()
	result := []coldB0ForwardRow{}
	for rows.Next() {
		var r coldB0ForwardRow
		var hash *int64
		var due time.Time
		if err := rows.Scan(&r.ID, &r.BoardID, &r.SourceURL, &hash, &due, &r.Interval); err != nil {
			return nil, "", err
		}
		r.Domain, err = coldRollbackSourceDomain(r.SourceURL)
		if err != nil || !canonicalUUID.MatchString(r.ID) || !canonicalUUID.MatchString(r.BoardID) || r.Interval < 1 || r.Interval > 8760 {
			return nil, "", ErrAuthorityLost
		}
		r.DueScore, err = coldB0DueScore(due)
		if err != nil {
			return nil, "", err
		}
		if hash != nil {
			r.Hash = strconv.FormatInt(*hash, 10)
		}
		result = append(result, r)
	}
	if err := rows.Err(); err != nil {
		return nil, "", err
	}
	if len(result) == 0 || len(result) > 2048 {
		return nil, "", ErrAuthorityLost
	}
	body, _ := json.Marshal(result)
	h := sha256.Sum256(body)
	return result, hex.EncodeToString(h[:]), nil
}

func observeColdB0Forward(ctx context.Context, c *Client, t *ColdB0Target, p *coldPublication, rows []coldB0ForwardRow) (*coldB0ForwardSnapshot, string, error) {
	// Reuse the unchanged production audit and exact existing source witness,
	// appending read-only legacy schedules/metadata in the same command.
	scope := "local KEYS = {unpack(KEYS,1,7)}\nlocal ARGV = {unpack(ARGV,1,23+tonumber(ARGV[22]))}\n"
	script := "local function audited_b0()\n" + scope + t.lua + "\nend\nlocal function observed_b0()\n" + scope + coldB0RollbackLua + "\nend\n" + coldB0ForwardLua
	keys := append(t.keys(), ownershipProjectionKey, coldPublicationKey)
	args := append(t.auditArguments(p.plan.Epoch()), p.previous, p.previousMarker, len(rows))
	for _, r := range rows {
		args = append(args, r.ID, r.Domain)
	}
	raw, err := c.redis.Eval(ctx, script, keys, args...).Slice()
	if err != nil {
		var rejected redis.Error
		if errors.As(err, &rejected) {
			return nil, "", ErrAuthorityLost
		}
		return nil, "", ErrObservation
	}
	if len(raw) != 4 {
		return nil, "", ErrProtocol
	}
	b0 := &coldB0RollbackSnapshot{}
	b0.Records, err = coldRollbackPairs(raw[0])
	if err != nil {
		return nil, "", err
	}
	b0.Guards, err = coldRollbackPairs(raw[1])
	if err != nil || len(b0.Records) != len(b0.Guards) {
		return nil, "", ErrAuthorityLost
	}
	groups, ok := raw[2].([]any)
	if !ok || len(groups) != 4 {
		return nil, "", ErrProtocol
	}
	b0.Authority = make([][]string, 4)
	for i, group := range groups {
		members, ok := group.([]any)
		if !ok || len(members) > 65536 {
			return nil, "", ErrAuthorityLost
		}
		b0.Authority[i] = []string{}
		for _, member := range members {
			s, ok := member.(string)
			if !ok || len(s) > 1024 {
				return nil, "", ErrAuthorityLost
			}
			b0.Authority[i] = append(b0.Authority[i], s)
		}
		sort.Strings(b0.Authority[i])
	}
	legacy, ok := raw[3].([]any)
	if !ok || len(legacy) != len(rows) {
		return nil, "", ErrProtocol
	}
	snapshot := &coldB0ForwardSnapshot{B0: b0, Legacy: []coldB0ForwardLegacy{}}
	for _, item := range legacy {
		values, ok := item.([]any)
		if !ok || len(values) != 5 {
			return nil, "", ErrProtocol
		}
		config, err := coldRollbackPairs(values[0])
		if err != nil {
			return nil, "", err
		}
		scores := []string{}
		for _, value := range values[1:] {
			if value == nil {
				scores = append(scores, "")
				continue
			}
			s, ok := value.(string)
			if !ok {
				return nil, "", ErrProtocol
			}
			scores = append(scores, s)
		}
		snapshot.Legacy = append(snapshot.Legacy, coldB0ForwardLegacy{config, scores})
	}
	body, _ := json.Marshal(snapshot)
	h := sha256.Sum256(body)
	return snapshot, hex.EncodeToString(h[:]), nil
}

func coldB0ForwardMillis(score string) (int64, error) {
	if len(score) > 32 || !coldRollbackScore.MatchString(score) {
		return 0, ErrAuthorityLost
	}
	n, ok := new(big.Rat).SetString(score)
	if !ok || n.Sign() < 0 || n.Cmp(new(big.Rat).SetFrac64(9999999999999, 1000)) > 0 {
		return 0, ErrAuthorityLost
	}
	n.Mul(n, big.NewRat(1000, 1))
	q, rem := new(big.Int), new(big.Int)
	q.QuoRem(n.Num(), n.Denom(), rem)
	if rem.Sign() != 0 {
		q.Add(q, big.NewInt(1))
	}
	if !q.IsInt64() {
		return 0, ErrAuthorityLost
	}
	return q.Int64(), nil
}

func coldB0ForwardScore(raw string) (string, error) {
	f, err := strconv.ParseFloat(raw, 64)
	if err != nil || math.IsNaN(f) || math.IsInf(f, 0) || f < 0 || f > 9999999999.999 {
		return "", ErrAuthorityLost
	}
	if f == 0 {
		if n, ok := new(big.Rat).SetString(raw); !ok || n.Sign() != 0 {
			return "", ErrAuthorityLost
		}
	}
	s := strconv.FormatFloat(f, 'f', -1, 64)
	if len(s) > 32 {
		return "", ErrAuthorityLost
	}
	return s, nil
}

// Re-derive all producer request fields from the retained canonical row and
// exact source snapshot, both before approval and on read-only recovery.
func coldB0ForwardTaskFromSource(row coldB0ForwardRow, legacy coldB0ForwardLegacy, record coldB0RollbackRecord, guard string, target *ColdB0Target, epoch int64) (coldB0ForwardTask, bool, error) {
	exists := record.TaskID != ""
	var err error
	membership := []int{}
	for i, score := range legacy.Scores {
		if score != "" {
			membership = append(membership, i)
		}
	}
	config := map[string]string{"domain": row.Domain, "board_id": row.BoardID, "source_url": row.SourceURL, "description_r2_hash": row.Hash, "scrape_step": "0", "scrape_interval_hours": strconv.Itoa(row.Interval)}
	first, sourceScore := row.Hash == "", ""
	if !exists {
		if len(legacy.Config) == 0 && len(membership) == 0 {
			return coldB0ForwardTask{}, false, nil
		}
		if len(membership) != 1 || membership[0] < 2 || (len(legacy.Config) != 5 && len(legacy.Config) != 6) {
			return coldB0ForwardTask{}, false, ErrAuthorityLost
		}
		for key, value := range legacy.Config {
			if _, ok := config[key]; !ok || key != "description_r2_hash" && value != config[key] {
				return coldB0ForwardTask{}, false, ErrAuthorityLost
			}
		}
		for key := range config {
			if key != "scrape_interval_hours" {
				if _, ok := legacy.Config[key]; !ok {
					return coldB0ForwardTask{}, false, ErrAuthorityLost
				}
			}
		}
		hint := legacy.Config["description_r2_hash"]
		if hint != "" {
			parsed, err := strconv.ParseInt(hint, 10, 64)
			if err != nil || strconv.FormatInt(parsed, 10) != hint {
				return coldB0ForwardTask{}, false, ErrAuthorityLost
			}
		}
		config["description_r2_hash"] = hint
		first = membership[0] == 2
		sourceScore, err = coldB0ForwardScore(legacy.Scores[membership[0]])
		if err != nil {
			return coldB0ForwardTask{}, false, err
		}
	} else {
		if len(membership) != 0 {
			return coldB0ForwardTask{}, false, ErrAuthorityLost
		}
		previous, err := b0task.Decode(record.Payload, record.PayloadSHA256, b0task.Route{ShardID: target.document.ShardID, RoutingEpoch: epoch, EngineOwner: "go"})
		if err != nil {
			return coldB0ForwardTask{}, false, ErrAuthorityLost
		}
		kind, _, err := coldRollbackGuard(guard, target, previous)
		if err != nil {
			return coldB0ForwardTask{}, false, err
		}
		if record.State == "ready" {
			first = strings.HasPrefix(kind, "ft_")
		}
	}
	due := "0"
	if !first {
		due = row.DueScore
	}
	if sourceScore != "" {
		a, _ := new(big.Rat).SetString(sourceScore)
		b, _ := new(big.Rat).SetString(due)
		if first || a.Cmp(b) > 0 {
			due = sourceScore
		}
	}
	ms, err := coldB0ForwardMillis(due)
	if err != nil {
		return coldB0ForwardTask{}, false, err
	}
	task := coldB0ForwardTask{PostingID: row.ID, Domain: row.Domain, NextScrapeAtMS: ms, FirstTime: first, Config: config, LegacyScheduleScore: sourceScore, PostgresHash: row.Hash, ExistingState: record.State, ExistingPayloadSHA256: record.PayloadSHA256}
	return task, true, nil
}

func deriveColdB0ForwardPlan(ctx context.Context, tx pgx.Tx, c *Client, control coldB0ForwardControl, r ColdB0ForwardRequest, target *ColdB0Target) (*ColdB0ForwardPlan, error) {
	p, err := coldB0ForwardContext(ctx, tx, c, r, target)
	if err != nil {
		return nil, err
	}
	manifest, err := control.Manifest(ctx, target.document.Cohort)
	slugs := []string{}
	boardIDs := map[string]bool{}
	for _, b := range target.document.Boards {
		slugs = append(slugs, b.Slug)
		boardIDs[b.ID] = true
	}
	if err != nil || manifest.Outcome != "manifest" || manifest.Cohort != target.document.Cohort || !reflect.DeepEqual(manifest.BoardSlugs, slugs) || manifest.LifetimeCapacity != 2048 || manifest.LifetimeOccupancy < 0 || manifest.LifetimeHeadroom != 2048-manifest.LifetimeOccupancy {
		return nil, ErrAuthorityLost
	}
	rows, pgHash, err := coldB0ForwardRows(ctx, tx, target)
	if err != nil {
		return nil, err
	}
	snapshot, snapshotHash, err := observeColdB0Forward(ctx, c, target, p, rows)
	if err != nil {
		return nil, err
	}
	if int64(len(snapshot.B0.Records)) != manifest.LifetimeOccupancy {
		return nil, ErrAuthorityLost
	}
	records := map[string]coldB0RollbackRecord{}
	selected := map[string]bool{}
	for _, row := range rows {
		selected[row.ID] = true
	}
	retained := []string{}
	for id, raw := range snapshot.B0.Records {
		var record coldB0RollbackRecord
		d := json.NewDecoder(strings.NewReader(raw))
		d.UseNumber()
		value, err := coldJSONValue(d, 0)
		obj, ok := value.(map[string]any)
		if err != nil || !ok || len(obj) != 20 || d.Decode(new(any)) != io.EOF || json.Unmarshal([]byte(raw), &record) != nil || record.TaskID != id || !canonicalUUID.MatchString(id) || (record.State != "ready" && record.State != "terminal") {
			return nil, ErrAuthorityLost
		}
		task, err := b0task.Decode(record.Payload, record.PayloadSHA256, b0task.Route{ShardID: target.document.ShardID, RoutingEpoch: r.RoutingEpoch, EngineOwner: "go"})
		if err != nil || task.Envelope.TaskID != id || !boardIDs[task.Envelope.BoardID] {
			return nil, ErrAuthorityLost
		}
		if _, _, err := coldRollbackGuard(snapshot.B0.Guards[id], target, task); err != nil {
			return nil, err
		}
		if !selected[id] {
			if record.State != "terminal" {
				return nil, ErrAuthorityLost
			}
			retained = append(retained, id)
		}
		records[id] = record
	}
	sort.Strings(retained)
	for _, members := range snapshot.B0.Authority {
		for _, member := range members {
			if strings.HasPrefix(member, "scrape|") {
				at := strings.LastIndexByte(member, '|')
				if at >= 0 && (selected[member[at+1:]] || records[member[at+1:]].TaskID != "") {
					return nil, ErrAuthorityLost
				}
			}
		}
	}
	doc := coldB0ForwardDocument{Version: "jobseek.crawler.cold-b0-forward/v1", Request: r, TargetSHA256: target.digest, PostgresSHA256: pgHash, SnapshotSHA256: snapshotHash, Tasks: []coldB0ForwardTask{}, UnqueuedPostingIDs: []string{}, RetainedTerminalIDs: retained, LifetimeOccupancy: manifest.LifetimeOccupancy, LifetimeCapacity: 2048}
	doc.Target, doc.PostgresRows, doc.Snapshot = target.document, rows, snapshot
	for index, row := range rows {
		record, exists := records[row.ID]
		task, queued, err := coldB0ForwardTaskFromSource(row, snapshot.Legacy[index], record, snapshot.B0.Guards[row.ID], target, r.RoutingEpoch)
		if err != nil {
			return nil, err
		}
		if !queued {
			doc.UnqueuedPostingIDs = append(doc.UnqueuedPostingIDs, row.ID)
			continue
		}
		if !exists {
			doc.NewRecordCount++
		}
		prepared, err := control.Prepare(ctx, task.task())
		if err != nil || prepared.Outcome != "prepared" || !ownershipSHA256.MatchString(prepared.PreparationDigest) || !ownershipSHA256.MatchString(prepared.PayloadSHA256) || prepared.ExistingState != record.State || prepared.ExistingPayloadSHA256 != record.PayloadSHA256 {
			return nil, ErrAuthorityLost
		}
		task.PreparationDigest, task.PayloadSHA256 = prepared.PreparationDigest, prepared.PayloadSHA256
		doc.Tasks = append(doc.Tasks, task)
	}
	doc.ProjectedOccupancy = doc.LifetimeOccupancy + doc.NewRecordCount
	if len(doc.Tasks) == 0 || doc.NewRecordCount > manifest.LifetimeHeadroom || doc.ProjectedOccupancy > 1600 {
		return nil, ErrAuthorityLost
	}
	_, finalHash, err := observeColdB0Forward(ctx, c, target, p, rows)
	if err != nil {
		return nil, err
	}
	if finalHash != snapshotHash {
		return nil, ErrAuthorityLost
	}
	finalManifest, err := control.Manifest(ctx, target.document.Cohort)
	if err != nil || !reflect.DeepEqual(finalManifest, manifest) {
		return nil, ErrAuthorityLost
	}
	body, err := json.Marshal(doc)
	if err != nil || len(body) > 32*1024*1024 {
		return nil, ErrProtocol
	}
	h := sha256.Sum256(body)
	return decodeColdB0ForwardPlan(string(body), hex.EncodeToString(h[:]))
}

// BuildColdB0ForwardPlan performs only PG/Redis reads and authenticated producer
// preparation while holding the same exclusive barriers. It never activates,
// SAVE-s, selects ownership or attests host writer quiescence.
func BuildColdB0ForwardPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, r ColdB0ForwardRequest, target *ColdB0Target) (*ColdB0ForwardPlan, error) {
	if ctx == nil || c == nil || control == nil || target == nil || !r.valid() {
		return nil, ErrConfiguration
	}
	var result *ColdB0ForwardPlan
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		result, err = deriveColdB0ForwardPlan(ctx, tx, c, control, r, target)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}
