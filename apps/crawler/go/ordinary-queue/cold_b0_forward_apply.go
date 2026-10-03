package queue

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"reflect"
	"strconv"
	"strings"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type coldB0ForwardActivationControl interface {
	coldB0ForwardControl
	Activate(context.Context, b0producer.Task, string) (b0producer.Response, error)
}

func coldForwardRecord(raw string) (map[string]any, error) {
	d := json.NewDecoder(strings.NewReader(raw))
	d.UseNumber()
	v, err := coldJSONValue(d, 0)
	m, ok := v.(map[string]any)
	if err != nil || !ok || len(m) != 20 || d.Decode(new(any)) != io.EOF {
		return nil, ErrAuthorityLost
	}
	return m, nil
}

func coldForwardNumber(v any) (float64, error) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, ErrAuthorityLost
	}
	f, err := n.Float64()
	if err != nil || f < 0 || f > 9999999999999 {
		return 0, ErrAuthorityLost
	}
	return f, nil
}

func coldForwardRecordEqual(a, b map[string]any) bool {
	x, err := b0task.CanonicalJSON(a, false)
	y, other := b0task.CanonicalJSON(b, false)
	return err == nil && other == nil && string(x) == string(y)
}

// Match the pinned actual activate_legacy transition, including ready-record
// schedule advancement and exact source-score guards. Completed tasks are
// observed and skipped; no retry re-rounds a transferred fractional guard.
func coldForwardTaskTransferred(plan *ColdB0ForwardPlan, task coldB0ForwardTask, index int, current *coldB0ForwardSnapshot) bool {
	doc := plan.document
	oldRaw := doc.Snapshot.B0.Records[task.PostingID]
	raw := current.B0.Records[task.PostingID]
	record, err := coldForwardRecord(raw)
	if err != nil || record["payload_sha256"] != task.PayloadSHA256 || record["state"] != "ready" {
		return false
	}
	payload, ok := record["payload"].(string)
	if !ok {
		return false
	}
	decoded, err := b0task.Decode(payload, task.PayloadSHA256, b0task.Route{ShardID: doc.Target.ShardID, RoutingEpoch: doc.Request.RoutingEpoch, EngineOwner: "go"})
	if err != nil || decoded.Envelope.TaskID != task.PostingID || decoded.Envelope.BoardID != task.Config["board_id"] || decoded.Envelope.SourceURL != task.Config["source_url"] || decoded.Envelope.Domain != task.Domain {
		return false
	}
	var expected map[string]any
	kind := "recurring_browser"
	if task.FirstTime {
		kind = "ft_browser"
	}
	score := fmt.Sprintf("%.3f", float64(task.NextScrapeAtMS)/1000)
	if task.ExistingState == "ready" {
		expected, err = coldForwardRecord(oldRaw)
		if err != nil || expected["payload_sha256"] != task.PayloadSHA256 {
			return false
		}
		ready, err := coldForwardNumber(expected["ready_at_ms"])
		visible, other := coldForwardNumber(expected["visible_at_ms"])
		if err != nil || other != nil {
			return false
		}
		requested := float64(task.NextScrapeAtMS)
		if requested < ready {
			expected["ready_at_ms"] = json.Number(strconv.FormatInt(task.NextScrapeAtMS, 10))
			if requested < visible {
				expected["visible_at_ms"] = json.Number(strconv.FormatInt(task.NextScrapeAtMS, 10))
			}
		}
		parts := strings.Split(doc.Snapshot.B0.Guards[task.PostingID], "|")
		if len(parts) != 7 {
			return false
		}
		previous, err := strconv.ParseFloat(parts[6], 64)
		if err != nil {
			return false
		}
		previous *= 1000
		previousFirst := strings.HasPrefix(parts[5], "ft_")
		if previous < requested || previous == requested && (previousFirst || !task.FirstTime) {
			kind = "recurring_browser"
			if previousFirst {
				kind = "ft_browser"
			}
			score = fmt.Sprintf("%.3f", previous/1000)
		}
	} else {
		wantedRevision := int64(1)
		if task.ExistingState == "terminal" {
			var original coldB0RollbackRecord
			if json.Unmarshal([]byte(oldRaw), &original) != nil {
				return false
			}
			old, err := b0task.Decode(original.Payload, original.PayloadSHA256, b0task.Route{ShardID: doc.Target.ShardID, RoutingEpoch: doc.Request.RoutingEpoch, EngineOwner: "go"})
			if err != nil {
				return false
			}
			wantedRevision = old.Envelope.ConfigRevision + 1
		} else if task.ExistingState != "" {
			return false
		}
		if decoded.Envelope.ConfigRevision != wantedRevision || decoded.Envelope.InitialReadyAtMS != task.NextScrapeAtMS {
			return false
		}
		e := decoded.Envelope
		number := func(v int64) json.Number { return json.Number(strconv.FormatInt(v, 10)) }
		expected = map[string]any{"task_id": task.PostingID, "task_kind": "scrape", "state": "ready", "shard_id": e.ShardID, "routing_epoch": number(e.RoutingEpoch), "engine_owner": "go", "config_revision": number(e.ConfigRevision), "policy_key": e.PolicyKey, "domain": task.Domain, "payload": payload, "payload_sha256": task.PayloadSHA256, "payload_sha1": decoded.PayloadSHA1, "claim_token": nil, "claim_sequence": nil, "lease_until_ms": nil, "ready_at_ms": number(task.NextScrapeAtMS), "visible_at_ms": number(task.NextScrapeAtMS), "failures": number(0), "pending_ready_at_ms": nil, "pending_first_time": nil}
		if task.ExistingState == "" {
			for i, source := range doc.Snapshot.Legacy[index].Scores {
				if source != "" {
					score = source
					if i == 2 {
						kind = "ft_browser"
					} else if i == 3 {
						kind = "recurring_browser"
					} else {
						return false
					}
				}
			}
		}
	}
	guard := strings.Join([]string{doc.Target.Namespace, doc.Target.ShardID, strconv.FormatInt(doc.Request.RoutingEpoch, 10), task.Config["board_id"], task.Domain, kind, score}, "|")
	return coldForwardRecordEqual(expected, record) && current.B0.Guards[task.PostingID] == guard && reflect.DeepEqual(current.Legacy[index].Config, task.Config) && strings.Join(current.Legacy[index].Scores, "") == ""
}

// Every record/guard/legacy row is either the exact retained source or the exact
// approved transition. Terminal and pruned work, foreign records and suffix
// authority cannot disappear or silently become replacement inventory.
func classifyColdB0Forward(plan *ColdB0ForwardPlan, current *coldB0ForwardSnapshot) ([]bool, error) {
	doc := plan.document
	if current == nil || current.B0 == nil || len(current.Legacy) != len(doc.PostgresRows) || !reflect.DeepEqual(current.B0.Authority, doc.Snapshot.B0.Authority) || len(current.B0.Records) != len(current.B0.Guards) {
		return nil, ErrAuthorityLost
	}
	indexes := map[string]int{}
	for i, row := range doc.PostgresRows {
		indexes[row.ID] = i
	}
	tasks := map[string]bool{}
	done := make([]bool, len(doc.Tasks))
	expectedCount := len(doc.Snapshot.B0.Records)
	for i, task := range doc.Tasks {
		tasks[task.PostingID] = true
		index := indexes[task.PostingID]
		before := current.B0.Records[task.PostingID] == doc.Snapshot.B0.Records[task.PostingID] && current.B0.Guards[task.PostingID] == doc.Snapshot.B0.Guards[task.PostingID] && reflect.DeepEqual(current.Legacy[index], doc.Snapshot.Legacy[index])
		done[i] = coldForwardTaskTransferred(plan, task, index, current)
		if !before && !done[i] {
			return nil, ErrAuthorityLost
		}
		if done[i] && task.ExistingState == "" {
			expectedCount++
		}
	}
	for id, raw := range doc.Snapshot.B0.Records {
		if !tasks[id] && (current.B0.Records[id] != raw || current.B0.Guards[id] != doc.Snapshot.B0.Guards[id]) {
			return nil, ErrAuthorityLost
		}
	}
	for i, row := range doc.PostgresRows {
		if !tasks[row.ID] && !reflect.DeepEqual(current.Legacy[i], doc.Snapshot.Legacy[i]) {
			return nil, ErrAuthorityLost
		}
	}
	for id := range current.B0.Records {
		if !tasks[id] && doc.Snapshot.B0.Records[id] == "" {
			return nil, ErrAuthorityLost
		}
	}
	if len(current.B0.Records) != expectedCount {
		return nil, ErrAuthorityLost
	}
	return done, nil
}

func attestColdForwardManifest(ctx context.Context, control coldB0ForwardControl, target *ColdB0Target, snapshot *coldB0ForwardSnapshot) error {
	m, err := control.Manifest(ctx, target.document.Cohort)
	slugs := []string{}
	for _, b := range target.document.Boards {
		slugs = append(slugs, b.Slug)
	}
	if err != nil || m.Outcome != "manifest" || m.Cohort != target.document.Cohort || !reflect.DeepEqual(m.BoardSlugs, slugs) || m.LifetimeCapacity != 2048 || m.LifetimeOccupancy != int64(len(snapshot.B0.Records)) || m.LifetimeHeadroom != 2048-m.LifetimeOccupancy {
		return ErrAuthorityLost
	}
	return nil
}

func applyColdB0Forward(ctx context.Context, tx pgx.Tx, c *Client, control coldB0ForwardActivationControl, plan *ColdB0ForwardPlan, target *ColdB0Target) (*coldB0ForwardSnapshot, error) {
	if plan.document.TargetSHA256 != target.digest {
		return nil, ErrAuthorityLost
	}
	p, err := coldB0ForwardContext(ctx, tx, c, plan.Request(), target)
	if err != nil {
		return nil, err
	}
	return applyObservedColdB0Forward(ctx, tx, c, control, plan, target, p)
}

func applyObservedColdB0Forward(ctx context.Context, tx pgx.Tx, c *Client, control coldB0ForwardActivationControl, plan *ColdB0ForwardPlan, target *ColdB0Target, p *coldPublication) (*coldB0ForwardSnapshot, error) {
	rows, hash, err := coldB0ForwardRows(ctx, tx, target)
	if err != nil {
		return nil, err
	}
	if hash != plan.document.PostgresSHA256 || !reflect.DeepEqual(rows, plan.document.PostgresRows) {
		return nil, ErrAuthorityLost
	}
	snapshot, _, err := observeColdB0Forward(ctx, c, target, p, rows)
	if err != nil {
		return nil, err
	}
	done, err := classifyColdB0Forward(plan, snapshot)
	if err != nil {
		return nil, err
	}
	if err := attestColdForwardManifest(ctx, control, target, snapshot); err != nil {
		return nil, err
	}
	for i, task := range plan.document.Tasks {
		if done[i] {
			continue
		}
		// One explicit approved attempt. An uncertain reply returns contained;
		// the next operator invocation classifies the actual effect and skips it.
		r, err := control.Activate(ctx, task.task(), task.PreparationDigest)
		if err != nil {
			return nil, ErrObservation
		}
		if r.Outcome != "activated" || r.PreparationDigest != task.PreparationDigest || r.PayloadSHA256 != task.PayloadSHA256 {
			return nil, ErrAuthorityLost
		}
		snapshot, _, err = observeColdB0Forward(ctx, c, target, p, rows)
		if err != nil {
			return nil, err
		}
		done, err = classifyColdB0Forward(plan, snapshot)
		if err != nil || !done[i] {
			return nil, ErrAuthorityLost
		}
	}
	for _, transferred := range done {
		if !transferred {
			return nil, ErrAuthorityLost
		}
	}
	if int64(len(snapshot.B0.Records)) != plan.document.ProjectedOccupancy {
		return nil, ErrAuthorityLost
	}
	if err := attestColdForwardManifest(ctx, control, target, snapshot); err != nil {
		return nil, err
	}
	return snapshot, nil
}

// ApplyColdB0ForwardPlan applies only immutable retained approval. It remains
// confined to the reserved joint journal; it cannot publish/start ownership.
// Completion requires SAVE/readback and separate durable evidence below.
func ApplyColdB0ForwardPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, digest, revision string, target *ColdB0Target) (*ColdB0ForwardApplication, error) {
	if ctx == nil || c == nil || control == nil || target == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return nil, ErrConfiguration
	}
	return applyRetainedColdB0Forward(ctx, pool, c, control, digest, revision, target)
}
