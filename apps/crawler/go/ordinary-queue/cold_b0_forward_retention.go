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
	"reflect"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed cold_b0_forward.sql
var coldB0ForwardSchema string

func coldB0ForwardHash(v any) string {
	body, _ := json.Marshal(v)
	h := sha256.Sum256(body)
	return hex.EncodeToString(h[:])
}

func decodeColdB0ForwardPlan(body, digest string) (*ColdB0ForwardPlan, error) {
	h := sha256.Sum256([]byte(body))
	if len(body) < 1 || len(body) > 32*1024*1024 || !ownershipSHA256.MatchString(digest) || hex.EncodeToString(h[:]) != digest {
		return nil, ErrAuthorityLost
	}
	var doc coldB0ForwardDocument
	d := json.NewDecoder(strings.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(&doc) != nil || d.Decode(new(any)) != io.EOF || doc.Version != "jobseek.crawler.cold-b0-forward/v1" || !doc.Request.valid() || !validColdB0(doc.Target) || coldB0ForwardHash(doc.Target) != doc.TargetSHA256 || coldB0ForwardHash(doc.PostgresRows) != doc.PostgresSHA256 || doc.Snapshot == nil || doc.Snapshot.B0 == nil || coldB0ForwardHash(doc.Snapshot) != doc.SnapshotSHA256 || doc.Tasks == nil || doc.UnqueuedPostingIDs == nil || doc.RetainedTerminalIDs == nil || len(doc.Tasks) < 1 || len(doc.PostgresRows) > 2048 || len(doc.PostgresRows) < len(doc.Tasks) || len(doc.Snapshot.Legacy) != len(doc.PostgresRows) || doc.Snapshot.B0.Records == nil || doc.Snapshot.B0.Guards == nil || len(doc.Snapshot.B0.Records) != len(doc.Snapshot.B0.Guards) || len(doc.Snapshot.B0.Authority) != 4 || doc.LifetimeCapacity != 2048 || doc.LifetimeOccupancy != int64(len(doc.Snapshot.B0.Records)) || doc.NewRecordCount < 0 || doc.ProjectedOccupancy != doc.LifetimeOccupancy+doc.NewRecordCount || doc.ProjectedOccupancy > 1600 {
		return nil, ErrAuthorityLost
	}
	boards := map[string]bool{}
	for _, b := range doc.Target.Boards {
		boards[b.ID] = true
	}
	rows := map[string]coldB0ForwardRow{}
	indexes := map[string]int{}
	for i, row := range doc.PostgresRows {
		domain, err := coldRollbackSourceDomain(row.SourceURL)
		if err != nil || domain != row.Domain || !canonicalUUID.MatchString(row.ID) || !boards[row.BoardID] || i > 0 && doc.PostgresRows[i-1].ID >= row.ID || row.Interval < 1 || row.Interval > 8760 {
			return nil, ErrAuthorityLost
		}
		if _, err := coldB0ForwardMillis(row.DueScore); err != nil {
			return nil, ErrAuthorityLost
		}
		if row.Hash != "" {
			v, err := strconv.ParseInt(row.Hash, 10, 64)
			if err != nil || strconv.FormatInt(v, 10) != row.Hash {
				return nil, ErrAuthorityLost
			}
		}
		rows[row.ID], indexes[row.ID] = row, i
	}
	records := map[string]coldB0RollbackRecord{}
	target := &ColdB0Target{document: doc.Target}
	for id, raw := range doc.Snapshot.B0.Records {
		var record coldB0RollbackRecord
		decoder := json.NewDecoder(strings.NewReader(raw))
		decoder.UseNumber()
		value, err := coldJSONValue(decoder, 0)
		object, ok := value.(map[string]any)
		if err != nil || !ok || len(object) != 20 || decoder.Decode(new(any)) != io.EOF || !canonicalUUID.MatchString(id) || !utf8.ValidString(raw) || json.Unmarshal([]byte(raw), &record) != nil || record.TaskID != id || (record.State != "ready" && record.State != "terminal") {
			return nil, ErrAuthorityLost
		}
		task, err := b0task.Decode(record.Payload, record.PayloadSHA256, b0task.Route{ShardID: doc.Target.ShardID, RoutingEpoch: doc.Request.RoutingEpoch, EngineOwner: "go"})
		if err != nil || task.Envelope.TaskID != id || !boards[task.Envelope.BoardID] {
			return nil, ErrAuthorityLost
		}
		if _, _, err := coldRollbackGuard(doc.Snapshot.B0.Guards[id], target, task); err != nil {
			return nil, ErrAuthorityLost
		}
		records[id] = record
	}
	for _, legacy := range doc.Snapshot.Legacy {
		if legacy.Config == nil || len(legacy.Config) > 6 || len(legacy.Scores) != 4 {
			return nil, ErrAuthorityLost
		}
		for k, v := range legacy.Config {
			if !utf8.ValidString(k) || !utf8.ValidString(v) {
				return nil, ErrAuthorityLost
			}
		}
		for _, score := range legacy.Scores {
			if score != "" {
				if _, err := coldB0ForwardScore(score); err != nil {
					return nil, ErrAuthorityLost
				}
			}
		}
	}
	seen := map[string]bool{}
	newCount := int64(0)
	for i, t := range doc.Tasks {
		row, ok := rows[t.PostingID]
		if !ok || i > 0 && doc.Tasks[i-1].PostingID >= t.PostingID || t.Domain != row.Domain || t.PostgresHash != row.Hash || len(t.Config) != 6 || t.Config["domain"] != row.Domain || t.Config["board_id"] != row.BoardID || t.Config["source_url"] != row.SourceURL || t.Config["scrape_step"] != "0" || t.Config["scrape_interval_hours"] != strconv.Itoa(row.Interval) || !ownershipSHA256.MatchString(t.PreparationDigest) || !ownershipSHA256.MatchString(t.PayloadSHA256) {
			return nil, ErrAuthorityLost
		}
		if hint := t.Config["description_r2_hash"]; hint != "" {
			v, err := strconv.ParseInt(hint, 10, 64)
			if err != nil || strconv.FormatInt(v, 10) != hint {
				return nil, ErrAuthorityLost
			}
		}
		if _, err := b0producer.EncodeRequest(b0producer.Request{Version: b0producer.Protocol, Operation: "prepare", Domain: t.Domain, PostingID: t.PostingID, NextScrapeAtMS: t.NextScrapeAtMS, Config: t.Config, Browser: true, FirstTime: t.FirstTime, OperatorTransfer: true, LegacyScheduleScore: t.LegacyScheduleScore}); err != nil {
			return nil, ErrAuthorityLost
		}
		record, exists := records[t.PostingID]
		if t.ExistingState != record.State || t.ExistingPayloadSHA256 != record.PayloadSHA256 {
			return nil, ErrAuthorityLost
		}
		if !exists {
			newCount++
			if t.LegacyScheduleScore == "" {
				return nil, ErrAuthorityLost
			}
			if _, err := coldB0ForwardMillis(t.LegacyScheduleScore); err != nil {
				return nil, ErrAuthorityLost
			}
		} else if t.LegacyScheduleScore != "" {
			return nil, ErrAuthorityLost
		}
		derived, queued, err := coldB0ForwardTaskFromSource(row, doc.Snapshot.Legacy[indexes[t.PostingID]], record, doc.Snapshot.B0.Guards[t.PostingID], target, doc.Request.RoutingEpoch)
		if err != nil || !queued {
			return nil, ErrAuthorityLost
		}
		derived.PreparationDigest, derived.PayloadSHA256 = t.PreparationDigest, t.PayloadSHA256
		if !reflect.DeepEqual(derived, t) {
			return nil, ErrAuthorityLost
		}
		seen[t.PostingID] = true
	}
	if newCount != doc.NewRecordCount {
		return nil, ErrAuthorityLost
	}
	for i, id := range doc.UnqueuedPostingIDs {
		if _, ok := rows[id]; !ok || seen[id] || i > 0 && doc.UnqueuedPostingIDs[i-1] >= id || records[id].TaskID != "" {
			return nil, ErrAuthorityLost
		}
		legacy := doc.Snapshot.Legacy[indexes[id]]
		if len(legacy.Config) != 0 || strings.Join(legacy.Scores, "") != "" {
			return nil, ErrAuthorityLost
		}
		seen[id] = true
	}
	if len(seen) != len(rows) {
		return nil, ErrAuthorityLost
	}
	retained := map[string]bool{}
	for i, id := range doc.RetainedTerminalIDs {
		if !canonicalUUID.MatchString(id) || seen[id] || records[id].State != "terminal" || i > 0 && doc.RetainedTerminalIDs[i-1] >= id {
			return nil, ErrAuthorityLost
		}
		retained[id] = true
	}
	for id := range records {
		if !seen[id] && !retained[id] {
			return nil, ErrAuthorityLost
		}
	}
	for _, members := range doc.Snapshot.B0.Authority {
		if members == nil || len(members) > 65536 {
			return nil, ErrAuthorityLost
		}
		for i, member := range members {
			if len(member) > 1024 || i > 0 && members[i-1] >= member {
				return nil, ErrAuthorityLost
			}
			if strings.HasPrefix(member, "scrape|") {
				at := strings.LastIndexByte(member, '|')
				if at >= 0 && (seen[member[at+1:]] || records[member[at+1:]].TaskID != "") {
					return nil, ErrAuthorityLost
				}
			}
		}
	}
	wanted, err := json.Marshal(doc)
	if err != nil || !bytes.Equal(wanted, []byte(body)) {
		return nil, ErrAuthorityLost
	}
	return &ColdB0ForwardPlan{doc, body, digest}, nil
}

func loadColdB0ForwardPlan(ctx context.Context, tx pgx.Tx, digest, revision string) (*ColdB0ForwardPlan, error) {
	var body string
	err := tx.QueryRow(ctx, "SELECT payload FROM crawler_ownership_b0_forward WHERE plan_sha256=$1 AND source_revision=$2", digest, revision).Scan(&body)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrAuthorityLost
	}
	if err != nil {
		return nil, err
	}
	p, err := decodeColdB0ForwardPlan(body, digest)
	if err != nil || p.document.Request.SourceRevision != revision {
		return nil, ErrAuthorityLost
	}
	return p, nil
}

func retainColdB0ForwardPlan(ctx context.Context, tx pgx.Tx, c *Client, control coldB0ForwardControl, r ColdB0ForwardRequest, target *ColdB0Target, approved string) (*ColdB0ForwardPlan, error) {
	p, err := deriveColdB0ForwardPlan(ctx, tx, c, control, r, target)
	if err != nil {
		return nil, err
	}
	if p.digest != approved {
		return nil, ErrAuthorityLost
	}
	if _, err := decodeColdB0ForwardPlan(p.body, p.digest); err != nil {
		return nil, err
	}
	if _, err := tx.Exec(ctx, "INSERT INTO crawler_ownership_b0_target(target_sha256,payload) VALUES($1,$2) ON CONFLICT(target_sha256) DO NOTHING", target.digest, target.body); err != nil {
		return nil, err
	}
	var retainedTarget string
	if err := tx.QueryRow(ctx, "SELECT payload FROM crawler_ownership_b0_target WHERE target_sha256=$1", target.digest).Scan(&retainedTarget); err != nil {
		return nil, err
	}
	if retainedTarget != target.body {
		return nil, ErrAuthorityLost
	}
	if _, err := tx.Exec(ctx, `INSERT INTO crawler_ownership_b0_forward(plan_sha256,intent_sha256,source_revision,routing_epoch,ordinary_plan_sha256,target_sha256,payload)
 VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(intent_sha256) DO NOTHING`, p.digest, r.IntentSHA256, r.SourceRevision, r.RoutingEpoch, r.OrdinaryPlanSHA256, target.digest, p.body); err != nil {
		return nil, err
	}
	retained, err := loadColdB0ForwardPlan(ctx, tx, approved, r.SourceRevision)
	if err != nil {
		return nil, err
	}
	if retained.body != p.body {
		return nil, ErrAuthorityLost
	}
	return retained, nil
}

// RetainColdB0ForwardPlan commits a complete freshly re-derived approved
// manifest before any activation. The same intent cannot select another plan.
// Use exact inspection/recovery after mutation; retention cannot reconstruct a
// manifest from partially transferred or lost queue evidence.
func RetainColdB0ForwardPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, r ColdB0ForwardRequest, target *ColdB0Target, approved string) (*ColdB0ForwardPlan, error) {
	if ctx == nil || c == nil || control == nil || target == nil || !r.valid() || !ownershipSHA256.MatchString(approved) {
		return nil, ErrConfiguration
	}
	var result *ColdB0ForwardPlan
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		result, err = retainColdB0ForwardPlan(ctx, tx, c, control, r, target, approved)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

// InspectColdB0ForwardPlan reads retained history without adoption, epoch locks,
// producer calls, Redis effects or permission to publish/release ownership.
func InspectColdB0ForwardPlan(ctx context.Context, pool *pgxpool.Pool, digest, revision string) (*ColdB0ForwardPlan, error) {
	if ctx == nil || pool == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return nil, ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	var result *ColdB0ForwardPlan
	err := pgx.BeginTxFunc(ctx, pool, pgx.TxOptions{AccessMode: pgx.ReadOnly}, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SET LOCAL statement_timeout='10s'"); err != nil {
			return err
		}
		var err error
		result, err = loadColdB0ForwardPlan(ctx, tx, digest, revision)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}
