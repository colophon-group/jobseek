package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

// The host must independently authenticate the release, stopped producer,
// original mutation flock, SQL restoration/zero fences and all-writer exclusion
// before delegating this root-owned decision. These identities do not grant
// host authority. The producer receives no SQL credentials or task authority.
type producerColdCleanupDecision struct {
	ActivationSentinelSHA256      string `json:"activation_sentinel_sha256"`
	AllWritersReceiptSHA256       string `json:"all_writers_receipt_sha256"`
	B0RestorationPlanSHA256       string `json:"b0_restoration_plan_sha256"`
	Cohort                        string `json:"cohort"`
	LuaSHA256                     string `json:"lua_sha256"`
	Namespace                     string `json:"namespace"`
	OrdinaryRestorationPlanSHA256 string `json:"ordinary_restoration_plan_sha256"`
	RetirementEpoch               int64  `json:"retirement_epoch"`
	ReversalSHA256                string `json:"reversal_sha256"`
	RuntimeImage                  string `json:"runtime_image"`
	Schema                        string `json:"schema"`
	ShardID                       string `json:"shard_id"`
	SourceEpoch                   int64  `json:"source_epoch"`
	SourceReceiptSHA256           string `json:"source_receipt_sha256"`
	SourceRevision                string `json:"source_revision"`
	SQLCleanupReceiptSHA256       string `json:"sql_cleanup_receipt_sha256"`
}

const producerColdCleanupSchema = "jobseek.lightpanda.producer-cold-cleanup/v1"

func decodeProducerColdCleanupDecision(body []byte, digest string, configured producerConfig, source string) (producerColdCleanupDecision, error) {
	var d producerColdCleanupDecision
	sum := sha256.Sum256(body)
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if len(body) == 0 || len(body) > 8192 || !hex256.MatchString(digest) || hex.EncodeToString(sum[:]) != digest || decoder.Decode(&d) != nil || decoder.Decode(new(any)) != io.EOF {
		return d, errors.New("invalid cold cleanup decision")
	}
	canonical, err := json.Marshal(d)
	if err != nil || !bytes.Equal(canonical, body) || d.Schema != producerColdCleanupSchema || !hex160.MatchString(source) || d.SourceRevision != source || d.Cohort != configured.Cohort || d.Namespace != configured.Namespace || d.ShardID != configured.Route.ShardID || configured.Route.EngineOwner != engineOwner || d.SourceEpoch != configured.Route.RoutingEpoch || d.SourceEpoch < 1 || d.RetirementEpoch <= d.SourceEpoch || d.RetirementEpoch > maxInteger || d.LuaSHA256 != expectedLuaSHA256 || !strings.HasPrefix(d.RuntimeImage, "ghcr.io/colophon-group/jobseek-crawler@sha256:") || !hex256.MatchString(strings.TrimPrefix(d.RuntimeImage, "ghcr.io/colophon-group/jobseek-crawler@sha256:")) {
		return d, errors.New("cold cleanup does not match fixed runtime identity")
	}
	for _, value := range []string{d.ActivationSentinelSHA256, d.AllWritersReceiptSHA256, d.B0RestorationPlanSHA256, d.OrdinaryRestorationPlanSHA256, d.ReversalSHA256, d.SourceReceiptSHA256, d.SQLCleanupReceiptSHA256} {
		if !hex256.MatchString(value) {
			return d, errors.New("cold cleanup lacks retained host identity")
		}
	}
	return d, nil
}

func runProducerColdCleanup(configured producerConfig, path, digest string) error {
	if os.Geteuid() != 10001 || os.Getegid() != 10001 || configured.ClientUID != 0 || configured.Socket != producerSocketPath || configured.RedisOptions == nil {
		return errors.New("cold cleanup requires fixed UID/GID10001 and root coordinator")
	}
	groups, err := os.Getgroups()
	if err != nil {
		return errors.New("cold cleanup supplementary groups unavailable")
	}
	for _, group := range groups {
		if group != 10001 {
			return errors.New("cold cleanup refuses extra supplementary groups")
		}
	}
	body, err := readProducerColdFile(path, 0, true)
	if err != nil {
		return err
	}
	d, err := decodeProducerColdCleanupDecision(body, digest, configured, sourceRevision)
	if err != nil {
		return err
	}
	release, err := acquireProducerLifecycleLock(filepath.Dir(configured.Socket))
	if err != nil {
		return err
	}
	defer release()
	// Serving and initialization share the same lock. A stale socket must be
	// resolved by the authenticated stop handoff before cleanup can begin.
	if _, err := os.Lstat(configured.Socket); !os.IsNotExist(err) {
		return errors.New("cold cleanup requires absent producer socket")
	}
	sentinel, err := rollbackSentinel(configured)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := redis.NewClient(configured.RedisOptions)
	defer client.Close()
	queue, err := newB0Queue(client, configured.LuaPath, configured.Namespace, configured.Route, configured.DefaultDelay, newMetrics())
	if err != nil {
		return err
	}
	prefix := filepath.Join(filepath.Dir(configured.Socket), ".cold-cleanup-v1-"+digest)
	return cleanupColdProducer(ctx, queue, sentinel, d, body, prefix, func(ctx context.Context) error {
		result, err := client.Save(ctx).Result()
		if err != nil || result != "OK" {
			return errors.New("cold cleanup SAVE not acknowledged")
		}
		return nil
	})
}

func observeColdCleanup(ctx context.Context, q *b0Queue, d producerColdCleanupDecision, allowAbsent bool) error {
	pipeline := q.client.Pipeline()
	types := []*redis.StatusCmd{}
	for _, key := range append(append([]string{}, q.keys...), legacyGuardKey) {
		types = append(types, pipeline.Type(ctx, key))
	}
	ownerType := pipeline.Type(ctx, producerOwnerKey)
	owner := pipeline.HGetAll(ctx, producerOwnerKey)
	if _, err := pipeline.Exec(ctx); err != nil {
		return errors.New("cold cleanup Redis observation failed")
	}
	for _, kind := range types {
		if kind.Val() != "none" {
			return errors.New("cold cleanup source queue/guard remains")
		}
	}
	if allowAbsent && ownerType.Val() == "none" {
		return nil
	}
	expected := map[string]string{"schema": "jobseek.lightpanda.producer-rollback/v1", "namespace": d.Namespace, "shard_id": d.ShardID, "routing_epoch": strconv.FormatInt(d.SourceEpoch, 10), "engine_owner": engineOwner, "cohort": d.Cohort, "rollback_plan_digest": d.B0RestorationPlanSHA256, "source_receipt_sha256": d.SourceReceiptSHA256}
	if ownerType.Val() != "hash" || !reflect.DeepEqual(owner.Val(), expected) {
		return errors.New("cold cleanup tombstone cycle changed")
	}
	return nil
}

// The caller holds the lifecycle lock and has decoded the protected decision.
// Request-before-effects and SAVE-before-completion allow only exact recovery.
func cleanupColdProducer(ctx context.Context, q *b0Queue, sentinel *producerActivationSentinel, d producerColdCleanupDecision, body []byte, prefix string, save func(context.Context) error) error {
	if ctx == nil || ctx.Err() != nil || q == nil || sentinel == nil || save == nil || q.namespace != d.Namespace || q.route.ShardID != d.ShardID || q.route.RoutingEpoch != d.SourceEpoch || q.route.EngineOwner != engineOwner {
		return errors.New("invalid cold cleanup")
	}
	requestPath, completePath := prefix+".request", prefix+".complete"
	retained, err := readProducerColdFile(requestPath, uint32(os.Geteuid()), false)
	if err != nil {
		if _, err := os.Lstat(requestPath); !os.IsNotExist(err) {
			return errors.New("unsafe cold cleanup history")
		}
		if _, err := os.Lstat(completePath); !os.IsNotExist(err) {
			return errors.New("cold cleanup completion lacks request")
		}
		if err := observeColdCleanup(ctx, q, d, false); err != nil {
			return err
		}
		// The initial request must bind the exact existing source sentinel.
		if err := sentinel.clearableForRollback(); err != nil {
			return err
		}
		marker, err := readProducerColdFile(sentinel.path, uint32(os.Geteuid()), false)
		sum := sha256.Sum256(marker)
		if err != nil || hex.EncodeToString(sum[:]) != d.ActivationSentinelSHA256 {
			return errors.New("cold cleanup source sentinel changed or absent")
		}
		if err := retainProducerColdFile(requestPath, body); err != nil {
			return err
		}
	} else if !bytes.Equal(retained, body) {
		return errors.New("cold cleanup retained decision changed")
	}
	if err := syncProducerSentinelDirectory(filepath.Dir(requestPath)); err != nil {
		return err
	}
	complete, err := readProducerColdFile(completePath, uint32(os.Geteuid()), false)
	if err == nil {
		state, stateErr := sentinel.state()
		if !bytes.Equal(complete, body) || stateErr != nil || state != producerSentinelAbsent {
			return errors.New("cold cleanup completion/sentinel changed")
		}
		if err := observeColdCleanup(ctx, q, d, true); err != nil {
			return err
		}
		if count, err := q.client.Exists(ctx, producerOwnerKey).Result(); err != nil || count != 0 {
			return errors.New("completed cold cleanup owner reappeared")
		}
		return syncProducerSentinelDirectory(filepath.Dir(completePath))
	}
	if _, err := os.Lstat(completePath); !os.IsNotExist(err) {
		return errors.New("unsafe cold cleanup completion")
	}
	if err := observeColdCleanup(ctx, q, d, true); err != nil {
		return err
	}
	// Pending history permits an absent sentinel after our earlier effect, but
	// never a substituted preparing/active marker from another attempt.
	if _, err := os.Lstat(sentinel.path); err == nil {
		marker, readErr := readProducerColdFile(sentinel.path, uint32(os.Geteuid()), false)
		sum := sha256.Sum256(marker)
		if readErr != nil || hex.EncodeToString(sum[:]) != d.ActivationSentinelSHA256 {
			return errors.New("pending cold cleanup source sentinel changed")
		}
	} else if !os.IsNotExist(err) {
		return errors.New("pending cold cleanup sentinel unavailable")
	}
	if err := sentinel.clearForRollback(); err != nil {
		return err
	}
	args := []any{"clear_rollback_tombstone", d.ShardID, strconv.FormatInt(d.SourceEpoch, 10), engineOwner, "", "0", "", "0", "0", "0", "", "", "", "64", q.defaultDelay, d.B0RestorationPlanSHA256, "0", d.Namespace, "", "1", d.Cohort, "0", d.SourceReceiptSHA256}
	reply, err := q.script.Run(ctx, q.client, q.keys, args...).Slice()
	if err != nil || len(reply) != 12 || reply[0] != "accepted" || (reply[1] != "rollback_tombstone_cleared" && reply[1] != "rollback_tombstone_already_cleared") {
		return errors.New("cold cleanup exact Lua clear failed")
	}
	if err := save(ctx); err != nil {
		return err
	}
	if err := observeColdCleanup(ctx, q, d, true); err != nil {
		return err
	}
	if count, err := q.client.Exists(ctx, producerOwnerKey).Result(); err != nil || count != 0 {
		return errors.New("cold cleanup owner remains")
	}
	if state, err := sentinel.state(); err != nil || state != producerSentinelAbsent {
		return errors.New("cold cleanup sentinel remains")
	}
	return retainProducerColdFile(completePath, body)
}
