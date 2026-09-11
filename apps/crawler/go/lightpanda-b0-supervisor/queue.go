package main

import (
	"bytes"
	"context"
	"crypto/sha1" // Redis 8 exposes SHA-1 for script and record compatibility. //nolint:gosec
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/redis/go-redis/v9"
)

const (
	maxInteger        = int64(9_999_999_999_999)
	maxPayload        = 128 * 1024
	maxLeaseTTL       = time.Hour
	queueScanLimit    = 64
	queuePolicyKey    = "lightpanda-b0-v1"
	expectedLuaSHA256 = "1722a9a412113c154e75a9185e70636dbe322cdacff706c8d0b3f7ae58d59ee2"
)

var (
	safeID       = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`)
	safeDomain   = regexp.MustCompile(`^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$`)
	safeRevision = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$`)
	hex256       = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

type routeIdentity struct {
	ShardID      string
	RoutingEpoch int64
	EngineOwner  string
}

func (r routeIdentity) validate() error {
	if !safeID.MatchString(r.ShardID) || r.RoutingEpoch < 1 || r.RoutingEpoch > maxInteger || r.EngineOwner != "go" {
		return errors.New("invalid Go B0 route")
	}
	return nil
}

type taskEnvelope struct {
	SchemaVersion          string          `json:"schema_version"`
	TaskKind               string          `json:"task_kind"`
	TaskID                 string          `json:"task_id"`
	BoardID                string          `json:"board_id"`
	SourceURL              string          `json:"source_url"`
	PolicyKey              string          `json:"policy_key"`
	Domain                 string          `json:"domain"`
	ShardID                string          `json:"shard_id"`
	RoutingEpoch           int64           `json:"routing_epoch"`
	EngineOwner            string          `json:"engine_owner"`
	ConfigRevision         int64           `json:"config_revision"`
	InitialReadyAtMS       int64           `json:"initial_ready_at_ms"`
	BrowserBackend         string          `json:"browser_backend"`
	RoutingRevision        string          `json:"routing_revision"`
	ScraperType            string          `json:"scraper_type"`
	ScraperStep            int64           `json:"scraper_step"`
	Render                 bool            `json:"render"`
	Wait                   string          `json:"wait"`
	WaitFallback           *string         `json:"wait_fallback"`
	TimeoutMS              int64           `json:"timeout_ms"`
	ParserConfig           json.RawMessage `json:"parser_config"`
	AssignmentDigestSHA256 string          `json:"assignment_digest_sha256"`
}

type queueTask struct {
	Envelope      taskEnvelope
	Payload       string
	PayloadSHA256 string
	PayloadSHA1   string
}

func decodeQueueTask(payload, expectedDigest string, route routeIdentity) (queueTask, error) {
	if len(payload) == 0 || len(payload) > maxPayload || !hex256.MatchString(expectedDigest) {
		return queueTask{}, errors.New("invalid task payload bounds")
	}
	digest := sha256.Sum256([]byte(payload))
	if hex.EncodeToString(digest[:]) != expectedDigest {
		return queueTask{}, errors.New("task payload digest mismatch")
	}
	decoder := json.NewDecoder(strings.NewReader(payload))
	decoder.DisallowUnknownFields()
	var envelope taskEnvelope
	if err := decoder.Decode(&envelope); err != nil {
		return queueTask{}, fmt.Errorf("decode task envelope: %w", err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return queueTask{}, errors.New("task payload has trailing JSON")
	}
	parsed, err := url.Parse(envelope.SourceURL)
	if err != nil || parsed.Scheme != "https" || parsed.Hostname() != envelope.Domain || parsed.User != nil || parsed.Fragment != "" || (parsed.Port() != "" && parsed.Port() != "443") {
		return queueTask{}, errors.New("invalid task source URL")
	}
	if envelope.SchemaVersion != "lightpanda-b0-task-v1" || envelope.TaskKind != "scrape" ||
		!safeID.MatchString(envelope.TaskID) || !safeID.MatchString(envelope.BoardID) ||
		envelope.PolicyKey != queuePolicyKey || !safeDomain.MatchString(envelope.Domain) ||
		envelope.ShardID != route.ShardID || envelope.RoutingEpoch != route.RoutingEpoch || envelope.EngineOwner != route.EngineOwner ||
		envelope.ConfigRevision < 1 || envelope.ConfigRevision > maxInteger || envelope.InitialReadyAtMS < 0 || envelope.InitialReadyAtMS > maxInteger ||
		envelope.BrowserBackend != "lightpanda" || !safeRevision.MatchString(envelope.RoutingRevision) ||
		envelope.ScraperType != "json-ld" || envelope.ScraperStep != 0 || !envelope.Render || envelope.Wait != "load" || envelope.WaitFallback != nil ||
		envelope.TimeoutMS < 1 || envelope.TimeoutMS > 120_000 || !hex256.MatchString(envelope.AssignmentDigestSHA256) ||
		len(envelope.ParserConfig) < 2 || envelope.ParserConfig[0] != '{' {
		return queueTask{}, errors.New("task envelope violates B0 identity")
	}
	canonicalAssignment, err := canonicalAssignmentJSON(envelope.ParserConfig)
	if err != nil {
		return queueTask{}, err
	}
	assignmentDigest := sha256.Sum256(canonicalAssignment)
	if hex.EncodeToString(assignmentDigest[:]) != envelope.AssignmentDigestSHA256 {
		return queueTask{}, errors.New("task assignment digest mismatch")
	}
	if err := validateParserConfig(envelope); err != nil {
		return queueTask{}, err
	}
	legacy := sha1.Sum([]byte(payload)) //nolint:gosec
	return queueTask{Envelope: envelope, Payload: payload, PayloadSHA256: expectedDigest, PayloadSHA1: hex.EncodeToString(legacy[:])}, nil
}

func canonicalAssignmentJSON(raw json.RawMessage) ([]byte, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, errors.New("task parser config is invalid")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return nil, errors.New("task parser config has trailing JSON")
	}
	var encoded bytes.Buffer
	encoder := json.NewEncoder(&encoded)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, errors.New("task parser config cannot be canonicalized")
	}
	plain := bytes.TrimSuffix(encoded.Bytes(), []byte{'\n'})
	result := make([]byte, 0, len(plain))
	for len(plain) != 0 {
		if plain[0] < utf8.RuneSelf {
			result = append(result, plain[0])
			plain = plain[1:]
			continue
		}
		runeValue, size := utf8.DecodeRune(plain)
		if runeValue == utf8.RuneError && size == 1 {
			return nil, errors.New("task parser config contains invalid UTF-8")
		}
		if runeValue <= 0xffff {
			result = append(result, fmt.Sprintf(`\u%04x`, runeValue)...)
		} else {
			value := runeValue - 0x10000
			result = append(result, fmt.Sprintf(`\u%04x\u%04x`, 0xd800+(value>>10), 0xdc00+(value&0x3ff))...)
		}
		plain = plain[size:]
	}
	return result, nil
}

func validateParserConfig(envelope taskEnvelope) error {
	var config map[string]json.RawMessage
	decoder := json.NewDecoder(strings.NewReader(string(envelope.ParserConfig)))
	if err := decoder.Decode(&config); err != nil || config == nil {
		return errors.New("task parser config is invalid")
	}
	allowed := set("browser_backend", "routing_revision", "render", "timeout", "wait", "wait_fallback", "defaults", "defaults_by_url", "enrich", "ignore_address_region", "ignore_date_posted", "ignore_locations", "ignore_valid_through")
	for key := range config {
		if _, ok := allowed[key]; !ok {
			return errors.New("task parser config has unknown fields")
		}
	}
	for _, key := range []string{"browser_backend", "routing_revision", "render", "timeout", "wait", "wait_fallback"} {
		if _, ok := config[key]; !ok {
			return errors.New("task parser config is incomplete")
		}
	}
	var backend, revision, wait string
	var render bool
	var timeout int64
	if json.Unmarshal(config["browser_backend"], &backend) != nil || backend != envelope.BrowserBackend ||
		json.Unmarshal(config["routing_revision"], &revision) != nil || revision != envelope.RoutingRevision ||
		json.Unmarshal(config["render"], &render) != nil || !render ||
		json.Unmarshal(config["timeout"], &timeout) != nil || timeout != envelope.TimeoutMS ||
		json.Unmarshal(config["wait"], &wait) != nil || wait != envelope.Wait ||
		string(config["wait_fallback"]) != "null" {
		return errors.New("task parser config disagrees with assignment")
	}
	return nil
}

type transition struct {
	Decision       string
	Reason         string
	ServerTimeMS   int64
	TaskID         string
	ClaimToken     string
	LeaseUntilMS   int64
	ConfigRevision int64
	PayloadSHA256  string
	Payload        string
	PolicyKey      string
	Value          int64
	SecondaryValue int64
}

func (t transition) accepted() bool { return t.Decision == "accepted" }

type lease struct {
	Task         queueTask
	ClaimToken   string
	LeaseUntilMS int64
}

type b0Queue struct {
	client       *redis.Client
	script       *redis.Script
	keys         []string
	route        routeIdentity
	defaultDelay string
	metrics      *metrics
}

func newB0Queue(client *redis.Client, luaPath, namespace string, route routeIdentity, defaultDelay string, metrics *metrics) (*b0Queue, error) {
	if client == nil || metrics == nil || !safeID.MatchString(namespace) {
		return nil, errors.New("invalid B0 queue configuration")
	}
	if err := route.validate(); err != nil {
		return nil, err
	}
	scriptBytes, err := os.ReadFile(luaPath)
	if err != nil {
		return nil, fmt.Errorf("read B0 lifecycle Lua: %w", err)
	}
	if len(scriptBytes) == 0 || len(scriptBytes) > 128*1024 {
		return nil, errors.New("B0 lifecycle Lua has invalid size")
	}
	scriptDigest := sha256.Sum256(scriptBytes)
	if hex.EncodeToString(scriptDigest[:]) != expectedLuaSHA256 {
		return nil, errors.New("B0 lifecycle Lua digest does not match the reviewed Go protocol")
	}
	tag := "lightpanda-b0:{" + namespace + "}"
	return &b0Queue{client: client, script: redis.NewScript(string(scriptBytes)), route: route, defaultDelay: defaultDelay, metrics: metrics, keys: []string{
		tag + ":route", tag + ":records", tag + ":ready", tag + ":inflight", tag + ":dead", tag + ":terminal", tag + ":origin-holders",
	}}, nil
}

func (q *b0Queue) call(ctx context.Context, operation string, task *queueTask, claimToken string, leaseTTL time.Duration, readyAtMS int64, maxFailures int64, expectedLeaseUntilMS int64) (transition, error) {
	if ctx == nil {
		return transition{}, errors.New("queue context is required")
	}
	taskID, revision, payload, sha256Digest, sha1Digest := "", int64(0), "", "", ""
	if task != nil {
		taskID, revision, payload, sha256Digest, sha1Digest = task.Envelope.TaskID, task.Envelope.ConfigRevision, task.Payload, task.PayloadSHA256, task.PayloadSHA1
		if readyAtMS == 0 && operation != "reschedule_at" {
			readyAtMS = task.Envelope.InitialReadyAtMS
		}
	}
	argv := []any{operation, q.route.ShardID, strconv.FormatInt(q.route.RoutingEpoch, 10), q.route.EngineOwner, taskID,
		strconv.FormatInt(revision, 10), claimToken, strconv.FormatInt(leaseTTL.Milliseconds(), 10), strconv.FormatInt(readyAtMS, 10), strconv.FormatInt(maxFailures, 10),
		payload, sha256Digest, sha1Digest, strconv.Itoa(queueScanLimit), q.defaultDelay, "", strconv.FormatInt(expectedLeaseUntilMS, 10)}
	result, err := q.script.Run(ctx, q.client, q.keys, argv...).Result()
	if err != nil {
		q.metrics.incQueue(operation, "transport_error")
		return transition{}, fmt.Errorf("B0 queue %s: %w", operation, err)
	}
	raw, ok := result.([]any)
	if !ok || len(raw) != 12 {
		q.metrics.incQueue(operation, "transport_error")
		return transition{}, errors.New("B0 queue returned malformed transition")
	}
	values := make([]string, 12)
	for index, value := range raw {
		switch typed := value.(type) {
		case string:
			values[index] = typed
		case []byte:
			values[index] = string(typed)
		default:
			q.metrics.incQueue(operation, "transport_error")
			return transition{}, errors.New("B0 queue returned non-text transition")
		}
	}
	parsed := transition{Decision: values[0], Reason: values[1], TaskID: values[3], ClaimToken: values[4], PayloadSHA256: values[7], Payload: values[8], PolicyKey: values[9]}
	for index, target := range map[int]*int64{2: &parsed.ServerTimeMS, 5: &parsed.LeaseUntilMS, 6: &parsed.ConfigRevision, 10: &parsed.Value, 11: &parsed.SecondaryValue} {
		if values[index] == "" {
			continue
		}
		value, parseErr := strconv.ParseInt(values[index], 10, 64)
		if parseErr != nil || value < 0 || value > maxInteger || strconv.FormatInt(value, 10) != values[index] {
			q.metrics.incQueue(operation, "transport_error")
			return transition{}, errors.New("B0 queue returned invalid integer")
		}
		*target = value
	}
	if parsed.Decision != "accepted" && parsed.Decision != "fenced" && parsed.Decision != "not_current" {
		q.metrics.incQueue(operation, "transport_error")
		return transition{}, errors.New("B0 queue returned invalid decision")
	}
	if err := validateTransitionReply(operation, parsed, values, q.route, task, claimToken, leaseTTL, readyAtMS, expectedLeaseUntilMS); err != nil {
		q.metrics.incQueue(operation, "transport_error")
		return transition{}, fmt.Errorf("B0 queue returned invalid %s reply: %w", operation, err)
	}
	q.metrics.incQueue(operation, parsed.Decision)
	return parsed, nil
}

var allowedReasons = map[string]map[string]map[string]struct{}{
	"initialize": {
		"accepted":    set("initialized", "already_initialized"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt"),
	},
	"claim_next": {
		"accepted":    set("claimed"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "invalid_lease_ttl", "invalid_claim_policy", "record_corrupt", "conservation_violation", "origin_holder_corrupt", "rate_limit_corrupt", "delay_corrupt", "numeric_overflow", "claim_sequence_exhausted", "no_work"),
	},
	"heartbeat": {
		"accepted":    set("lease_extended"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "record_corrupt", "conservation_violation", "state_mismatch", "origin_holder_corrupt", "invalid_task_identity", "invalid_lease_fence", "invalid_lease_ttl", "lease_expired", "numeric_overflow", "lease_not_extended"),
	},
	"complete": {
		"accepted":    set("completed"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "record_corrupt", "conservation_violation", "state_mismatch", "origin_holder_corrupt", "invalid_task_identity", "invalid_lease_fence", "lease_expired"),
	},
	"reschedule_at": {
		"accepted":    set("rescheduled"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "record_corrupt", "conservation_violation", "state_mismatch", "origin_holder_corrupt", "invalid_task_identity", "invalid_lease_fence", "invalid_ready_at", "lease_expired"),
	},
	"reap_expired": {
		"accepted":    set("reaped"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "invalid_reap_policy", "conservation_violation", "origin_holder_corrupt"),
	},
	"audit": {
		"accepted":    set("audit_ok"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "audit_too_large", "conservation_violation", "origin_holder_corrupt"),
	},
}

func set(values ...string) map[string]struct{} {
	result := make(map[string]struct{}, len(values))
	for _, value := range values {
		result[value] = struct{}{}
	}
	return result
}

func validateTransitionReply(operation string, result transition, fields []string, route routeIdentity, task *queueTask, claimToken string, leaseTTL time.Duration, readyAtMS, expectedLeaseUntilMS int64) error {
	decisions, ok := allowedReasons[operation]
	if !ok {
		return errors.New("unknown operation")
	}
	reasons, ok := decisions[result.Decision]
	if !ok {
		return errors.New("unexpected decision")
	}
	if _, ok := reasons[result.Reason]; !ok {
		return errors.New("unexpected reason")
	}
	if result.Reason != "redis_time_invalid" && result.ServerTimeMS < 1 {
		return errors.New("invalid server time")
	}
	if result.Decision != "accepted" {
		for index := 3; index < 12; index++ {
			if fields[index] != "" {
				return errors.New("rejected transition leaked fields")
			}
		}
		return nil
	}
	full := operation == "claim_next"
	fenced := operation == "heartbeat" || operation == "complete" || operation == "reschedule_at"
	if full || fenced {
		if result.TaskID == "" || result.ClaimToken == "" || result.LeaseUntilMS < 1 || result.ConfigRevision < 1 || !hex256.MatchString(result.PayloadSHA256) {
			return errors.New("missing fence echo")
		}
		if full {
			if result.Payload == "" || result.PolicyKey == "" {
				return errors.New("missing claimed payload")
			}
		} else if result.Payload != "" || result.PolicyKey != "" {
			return errors.New("unexpected payload echo")
		}
		tokenParts := strings.Split(result.ClaimToken, ":")
		if len(tokenParts) != 2 || tokenParts[0] != strconv.FormatInt(route.RoutingEpoch, 10) {
			return errors.New("claim token has the wrong routing epoch")
		}
		sequence, sequenceErr := strconv.ParseInt(tokenParts[1], 10, 64)
		if sequenceErr != nil || sequence < 1 || sequence > maxInteger || strconv.FormatInt(sequence, 10) != tokenParts[1] {
			return errors.New("claim token sequence is invalid")
		}
		if operation == "claim_next" || operation == "heartbeat" {
			if result.LeaseUntilMS != result.ServerTimeMS+leaseTTL.Milliseconds() {
				return errors.New("lease deadline math mismatch")
			}
		}
		if task != nil && (result.TaskID != task.Envelope.TaskID || result.ClaimToken != claimToken || result.ConfigRevision != task.Envelope.ConfigRevision || result.PayloadSHA256 != task.PayloadSHA256) {
			return errors.New("fence echo mismatch")
		}
		if operation == "heartbeat" && result.LeaseUntilMS <= expectedLeaseUntilMS {
			return errors.New("lease was not extended")
		}
		if (operation == "complete" || operation == "reschedule_at") && result.LeaseUntilMS != expectedLeaseUntilMS {
			return errors.New("terminal lease echo mismatch")
		}
	} else {
		for index := 3; index < 10; index++ {
			if fields[index] != "" {
				return errors.New("unexpected identity fields")
			}
		}
	}
	switch operation {
	case "reap_expired":
		if fields[10] == "" || fields[11] == "" || result.Value+result.SecondaryValue > queueScanLimit {
			return errors.New("invalid reap counts")
		}
	case "audit":
		if fields[10] == "" || fields[11] == "" || result.Value > 512 || result.SecondaryValue > result.Value {
			return errors.New("invalid audit counts")
		}
	case "reschedule_at":
		if fields[10] == "" || fields[11] != "" || result.Value != readyAtMS {
			return errors.New("invalid ready-at echo")
		}
	default:
		if fields[10] != "" || fields[11] != "" {
			return errors.New("unexpected value fields")
		}
	}
	return nil
}

func (q *b0Queue) initialize(ctx context.Context) error {
	result, err := q.call(ctx, "initialize", nil, "", 0, 0, 0, 0)
	if err != nil || !result.accepted() {
		return transitionError("initialize", result, err)
	}
	return nil
}

func (q *b0Queue) claim(ctx context.Context, ttl time.Duration) (*lease, transition, error) {
	result, err := q.call(ctx, "claim_next", nil, "", ttl, 0, 0, 0)
	if err != nil || !result.accepted() {
		return nil, result, err
	}
	task, err := decodeQueueTask(result.Payload, result.PayloadSHA256, q.route)
	if err != nil || result.TaskID != task.Envelope.TaskID || result.PolicyKey != task.Envelope.PolicyKey || result.ConfigRevision != task.Envelope.ConfigRevision {
		return nil, result, errors.New("claimed B0 payload failed identity validation")
	}
	expectedPrefix := strconv.FormatInt(q.route.RoutingEpoch, 10) + ":"
	if !strings.HasPrefix(result.ClaimToken, expectedPrefix) || result.LeaseUntilMS <= result.ServerTimeMS {
		return nil, result, errors.New("claimed B0 lease fence is invalid")
	}
	return &lease{Task: task, ClaimToken: result.ClaimToken, LeaseUntilMS: result.LeaseUntilMS}, result, nil
}

func (q *b0Queue) heartbeat(ctx context.Context, current *lease, ttl time.Duration) error {
	result, err := q.call(ctx, "heartbeat", &current.Task, current.ClaimToken, ttl, 0, 0, current.LeaseUntilMS)
	if err != nil || !result.accepted() || result.ClaimToken != current.ClaimToken || result.LeaseUntilMS <= current.LeaseUntilMS {
		return transitionError("heartbeat", result, err)
	}
	current.LeaseUntilMS = result.LeaseUntilMS
	return nil
}

func (q *b0Queue) terminal(ctx context.Context, current *lease, readyAtMS *int64) error {
	operation := "complete"
	value := int64(0)
	if readyAtMS != nil {
		operation, value = "reschedule_at", *readyAtMS
	}
	result, err := q.call(ctx, operation, &current.Task, current.ClaimToken, 0, value, 0, current.LeaseUntilMS)
	if err != nil || !result.accepted() || result.ClaimToken != current.ClaimToken {
		return transitionError(operation, result, err)
	}
	return nil
}

func (q *b0Queue) reap(ctx context.Context, maxFailures int64) error {
	result, err := q.call(ctx, "reap_expired", nil, "", 0, 0, maxFailures, 0)
	if err != nil || !result.accepted() {
		return transitionError("reap_expired", result, err)
	}
	return nil
}

func (q *b0Queue) audit(ctx context.Context) error {
	result, err := q.call(ctx, "audit", nil, "", 0, 0, 0, 0)
	if err != nil || !result.accepted() {
		return transitionError("audit", result, err)
	}
	return nil
}

func transitionError(operation string, result transition, err error) error {
	if err != nil {
		return err
	}
	return fmt.Errorf("B0 queue %s rejected: %s/%s", operation, result.Decision, result.Reason)
}
