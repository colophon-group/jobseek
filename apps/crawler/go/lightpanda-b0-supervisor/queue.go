package main

import (
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

	"github.com/redis/go-redis/v9"
)

const (
	maxInteger               = int64(9_999_999_999_999)
	maxPayload               = 128 * 1024
	maxLeaseTTL              = time.Hour
	queueScanLimit           = 64
	queueRecordLimit         = int64(2048)
	queuePilotOccupancyLimit = int64(1600)
	queuePolicyKey           = "lightpanda-b0-v1"
	producerOwnerKey         = "lightpanda-b0:producer-owner"
	legacyGuardKey           = "lightpanda-b0:legacy-guard"
	producerOwnerV1          = "jobseek.lightpanda.producer-owner/v1"
	expectedLuaSHA256        = "d287fab9e7522c6e23806589b8943af700c2eb0f887212d3e8a1e6131cc71ae7"
)

var (
	safeID       = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`)
	safeDomain   = regexp.MustCompile(`^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$`)
	safeRevision = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$`)
	safeDecimal  = regexp.MustCompile(`^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$`)
	hex256       = regexp.MustCompile(`^[0-9a-f]{64}$`)
	hex160       = regexp.MustCompile(`^[0-9a-f]{40}$`)
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
	value, err := parseCanonicalValue(raw)
	if err != nil {
		return nil, errors.New("task parser config is invalid")
	}
	encoded, err := canonicalJSON(value, true)
	if err != nil {
		return nil, errors.New("task parser config cannot be canonicalized")
	}
	return encoded, nil
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
	DueAtMS      int64
}

type storedTask struct {
	Task             queueTask
	State            string
	Failures         int64
	PendingReadyAtMS *int64
	PendingFirstTime *bool
}

type queueCapacityError struct {
	Reason    string
	Occupancy int64
	Capacity  int64
}

func (failure queueCapacityError) Error() string { return "B0 queue namespace is full" }

type queueConflictError struct{ Reason string }

func (failure queueConflictError) Error() string {
	return "B0 queue producer conflict: " + failure.Reason
}

type queueAuthorityError struct {
	class     string
	operation string
}

func (failure queueAuthorityError) Error() string {
	return "B0 queue authority lost: " + failure.class + "/" + failure.operation
}

func queueAuthority(class, operation string) error {
	return queueAuthorityError{class: class, operation: operation}
}

func queueRedisFailure(err error, operation string) error {
	if errors.Is(err, context.Canceled) {
		return context.Canceled
	}
	return queueAuthority("redis", operation)
}

type b0Queue struct {
	client       *redis.Client
	script       *redis.Script
	keys         []string
	route        routeIdentity
	namespace    string
	defaultDelay string
	metrics      *metrics
}

func newB0Queue(client *redis.Client, luaPath, namespace string, route routeIdentity, defaultDelay string, metrics *metrics) (*b0Queue, error) {
	if client == nil || metrics == nil || !safeID.MatchString(namespace) || !safeDecimal.MatchString(defaultDelay) {
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
	return &b0Queue{client: client, script: redis.NewScript(string(scriptBytes)), route: route, namespace: namespace, defaultDelay: defaultDelay, metrics: metrics, keys: []string{
		tag + ":route", tag + ":records", tag + ":ready", tag + ":inflight", tag + ":dead", tag + ":terminal", tag + ":origin-holders",
	}}, nil
}

func (q *b0Queue) call(ctx context.Context, operation string, task *queueTask, claimToken string, leaseTTL time.Duration, readyAtMS int64, maxFailures int64, expectedLeaseUntilMS int64) (transition, error) {
	return q.callWithProducer(ctx, operation, task, claimToken, leaseTTL, readyAtMS, maxFailures, expectedLeaseUntilMS, "", "", false, false, producerOwnerIdentity{})
}

func (q *b0Queue) callWithProducer(ctx context.Context, operation string, task *queueTask, claimToken string, leaseTTL time.Duration, readyAtMS int64, maxFailures int64, expectedLeaseUntilMS int64, previousPayloadSHA256, legacyConfig string, operatorTransfer, firstTime bool, owner producerOwnerIdentity) (transition, error) {
	if ctx == nil {
		return transition{}, errors.New("queue context is required")
	}
	taskID, revision, payload, sha256Digest, sha1Digest := "", int64(0), "", "", ""
	if task != nil {
		taskID, revision, payload, sha256Digest, sha1Digest = task.Envelope.TaskID, task.Envelope.ConfigRevision, task.Payload, task.PayloadSHA256, task.PayloadSHA1
		if readyAtMS == 0 && operation != "reschedule_at" && operation != "activate_legacy" {
			readyAtMS = task.Envelope.InitialReadyAtMS
		}
	}
	operator := "0"
	if operatorTransfer {
		operator = "1"
	}
	argv := []any{operation, q.route.ShardID, strconv.FormatInt(q.route.RoutingEpoch, 10), q.route.EngineOwner, taskID,
		strconv.FormatInt(revision, 10), claimToken, strconv.FormatInt(leaseTTL.Milliseconds(), 10), strconv.FormatInt(readyAtMS, 10), strconv.FormatInt(maxFailures, 10),
		payload, sha256Digest, sha1Digest, strconv.Itoa(queueScanLimit), q.defaultDelay, previousPayloadSHA256, strconv.FormatInt(expectedLeaseUntilMS, 10), q.namespace, legacyConfig, operator}
	scheduleIntent := "0"
	if firstTime {
		scheduleIntent = "1"
	}
	if owner.validate() == nil {
		argv = append(argv, owner.Cohort, strconv.Itoa(len(owner.BoardSlugs)), scheduleIntent)
		for _, slug := range owner.BoardSlugs {
			argv = append(argv, slug)
		}
	} else {
		argv = append(argv, "", "0", scheduleIntent)
	}
	result, err := q.script.Run(ctx, q.client, q.keys, argv...).Result()
	if err != nil {
		q.metrics.incQueue(operation, "transport_error")
		return transition{}, queueRedisFailure(err, operation)
	}
	raw, ok := result.([]any)
	if !ok || len(raw) != 12 {
		q.metrics.incQueue(operation, "transport_error")
		return transition{}, queueAuthority("corruption", operation)
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
			return transition{}, queueAuthority("corruption", operation)
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
			return transition{}, queueAuthority("corruption", operation)
		}
		*target = value
	}
	if parsed.Decision != "accepted" && parsed.Decision != "fenced" && parsed.Decision != "not_current" {
		q.metrics.incQueue(operation, "transport_error")
		return transition{}, queueAuthority("corruption", operation)
	}
	if err := validateTransitionReply(operation, parsed, values, q.route, task, claimToken, leaseTTL, readyAtMS, expectedLeaseUntilMS); err != nil {
		q.metrics.incQueue(operation, "transport_error")
		return transition{}, queueAuthority("corruption", operation)
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
	"initialize_producer": {
		"accepted":    set("initialized", "already_initialized"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt"),
	},
	"activate_legacy": {
		"accepted":    set("activated", "already_activated", "reactivated"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "config_revision_not_advanced", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "exclusive_go_owner_required", "invalid_task_envelope", "namespace_full", "pilot_occupancy_limit", "task_already_exists", "record_corrupt", "conservation_violation", "state_mismatch", "legacy_state_corrupt", "legacy_config_mismatch", "legacy_inflight", "legacy_deadletter", "legacy_membership_conflict", "guard_identity_mismatch", "invalid_legacy_config", "legacy_membership_missing"),
	},
	"claim_next": {
		"accepted":    set("claimed"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "invalid_lease_ttl", "invalid_claim_policy", "record_corrupt", "conservation_violation", "origin_holder_corrupt", "guard_identity_mismatch", "rate_limit_corrupt", "delay_corrupt", "numeric_overflow", "claim_sequence_exhausted", "no_work"),
	},
	"heartbeat": {
		"accepted":    set("lease_extended"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "record_corrupt", "conservation_violation", "state_mismatch", "origin_holder_corrupt", "guard_identity_mismatch", "invalid_task_identity", "invalid_lease_fence", "invalid_lease_ttl", "lease_expired", "numeric_overflow", "lease_not_extended"),
	},
	"complete": {
		"accepted":    set("completed"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "record_corrupt", "conservation_violation", "state_mismatch", "origin_holder_corrupt", "guard_identity_mismatch", "invalid_task_identity", "invalid_lease_fence", "lease_expired"),
	},
	"reschedule_at": {
		"accepted":    set("rescheduled"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "record_corrupt", "conservation_violation", "state_mismatch", "origin_holder_corrupt", "guard_identity_mismatch", "invalid_task_identity", "invalid_lease_fence", "invalid_ready_at", "lease_expired"),
	},
	"fail_at": {
		"accepted":    set("failed_rescheduled"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "record_corrupt", "conservation_violation", "state_mismatch", "origin_holder_corrupt", "guard_identity_mismatch", "invalid_task_identity", "invalid_lease_fence", "invalid_ready_at", "lease_expired", "failure_counter_exhausted"),
	},
	"reap_expired": {
		"accepted":    set("reaped"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "invalid_reap_policy", "conservation_violation", "origin_holder_corrupt", "guard_identity_mismatch"),
	},
	"audit": {
		"accepted":    set("audit_ok"),
		"fenced":      set("shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch", "config_revision_mismatch", "record_fence_mismatch", "claim_token_mismatch", "lease_deadline_mismatch", "payload_digest_mismatch"),
		"not_current": set("redis_time_invalid", "invalid_route", "namespace_corrupt", "audit_too_large", "conservation_violation", "origin_holder_corrupt", "guard_identity_mismatch"),
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
		if operation == "activate_legacy" && contains(set("namespace_full", "pilot_occupancy_limit"), result.Reason) &&
			result.Value <= queueRecordLimit && result.SecondaryValue == queueRecordLimit &&
			(result.Reason != "namespace_full" || result.Value == queueRecordLimit) &&
			(result.Reason != "pilot_occupancy_limit" || result.Value >= queuePilotOccupancyLimit && result.Value < queueRecordLimit) {
			for index := 3; index < 10; index++ {
				if fields[index] != "" {
					return errors.New("rejected capacity transition leaked identity fields")
				}
			}
			return nil
		}
		for index := 3; index < 12; index++ {
			if fields[index] != "" {
				return errors.New("rejected transition leaked fields")
			}
		}
		return nil
	}
	full := operation == "claim_next"
	fenced := operation == "heartbeat" || operation == "complete" || operation == "reschedule_at" || operation == "fail_at"
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
		if (operation == "complete" || operation == "reschedule_at" || operation == "fail_at") && result.LeaseUntilMS != expectedLeaseUntilMS {
			return errors.New("terminal lease echo mismatch")
		}
	} else if operation == "activate_legacy" {
		if task == nil || result.TaskID != task.Envelope.TaskID || result.ConfigRevision != task.Envelope.ConfigRevision || result.PayloadSHA256 != task.PayloadSHA256 ||
			fields[4] != "" || fields[5] != "" || fields[8] != "" || fields[9] != "" {
			return errors.New("invalid producer activation echo")
		}
	} else {
		for index := 3; index < 10; index++ {
			if fields[index] != "" {
				return errors.New("unexpected identity fields")
			}
		}
	}
	switch operation {
	case "claim_next":
		if fields[10] == "" || fields[11] != "" || result.Value > result.ServerTimeMS {
			return errors.New("invalid claim due-at echo")
		}
	case "reap_expired":
		if fields[10] == "" || fields[11] == "" || result.Value+result.SecondaryValue > queueScanLimit {
			return errors.New("invalid reap counts")
		}
	case "audit":
		if fields[10] == "" || fields[11] == "" || result.Value > queueRecordLimit || result.SecondaryValue > result.Value {
			return errors.New("invalid audit counts")
		}
	case "activate_legacy":
		if fields[10] == "" || fields[11] == "" || result.Value != readyAtMS || result.SecondaryValue > 1 {
			return errors.New("invalid producer activation counts")
		}
	case "reschedule_at", "fail_at":
		if fields[10] == "" || fields[11] != "" || result.Value > readyAtMS {
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

func (q *b0Queue) initializeProducer(ctx context.Context, owner producerOwnerIdentity) error {
	if owner.validate() != nil || owner.Namespace != q.namespace || owner.Route != q.route {
		return queueAuthority("corruption", "initialize_producer")
	}
	result, err := q.callWithProducer(
		ctx, "initialize_producer", nil, "", 0, 0, 0, 0, "", "", false, false, owner,
	)
	if err != nil || !result.accepted() {
		return transitionError("initialize_producer", result, err)
	}
	return nil
}

func (q *b0Queue) persistProducer(ctx context.Context) error {
	if ctx == nil {
		return queueAuthority("corruption", "persist_producer")
	}
	status, err := q.client.Save(ctx).Result()
	if err != nil {
		return queueRedisFailure(err, "persist_producer")
	}
	if status != "OK" {
		return queueAuthority("corruption", "persist_producer")
	}
	return nil
}

func (q *b0Queue) activateLegacy(ctx context.Context, task *queueTask, readyAtMS int64, legacyConfig, previousPayloadSHA256 string, operatorTransfer, firstTime bool, owner producerOwnerIdentity) (transition, error) {
	if task == nil || task.Envelope.EngineOwner != engineOwner || legacyConfig == "" ||
		(firstTime && readyAtMS != 0) ||
		(previousPayloadSHA256 != "" && !hex256.MatchString(previousPayloadSHA256)) || owner.validate() != nil ||
		owner.Namespace != q.namespace || owner.Route != q.route {
		return transition{}, errors.New("invalid B0 legacy activation")
	}
	result, err := q.callWithProducer(ctx, "activate_legacy", task, "", 0, readyAtMS, 0, 0, previousPayloadSHA256, legacyConfig, operatorTransfer, firstTime, owner)
	if err != nil || !result.accepted() {
		if err == nil && result.Decision == "not_current" && contains(set("namespace_full", "pilot_occupancy_limit"), result.Reason) {
			return result, queueCapacityError{Reason: result.Reason, Occupancy: result.Value, Capacity: result.SecondaryValue}
		}
		if err == nil && result.Decision == "not_current" && contains(set("state_mismatch", "task_already_exists"), result.Reason) {
			return result, queueConflictError{Reason: result.Reason}
		}
		return result, transitionError("activate_legacy", result, err)
	}
	return result, nil
}

func (q *b0Queue) preflight(ctx context.Context, full bool, owner producerOwnerIdentity) (bool, error) {
	if ctx == nil || owner.validate() != nil || owner.Namespace != q.namespace || owner.Route != q.route {
		return false, queueAuthority("corruption", "preflight")
	}
	pipeline := q.client.Pipeline()
	types := make([]*redis.StatusCmd, 0, len(q.keys))
	for _, key := range q.keys {
		types = append(types, pipeline.Type(ctx, key))
	}
	if _, err := pipeline.Exec(ctx); err != nil {
		return false, queueRedisFailure(err, "preflight")
	}
	if types[0].Val() == "none" {
		for _, command := range types[1:] {
			if command.Val() != "none" {
				return false, queueAuthority("corruption", "preflight")
			}
		}
		globalPipeline := q.client.Pipeline()
		ownerType := globalPipeline.Type(ctx, producerOwnerKey)
		guardType := globalPipeline.Type(ctx, legacyGuardKey)
		if _, err := globalPipeline.Exec(ctx); err != nil {
			return false, queueRedisFailure(err, "preflight")
		}
		if ownerType.Val() != "none" || guardType.Val() != "none" {
			return false, queueAuthority("corruption", "preflight")
		}
		return true, nil
	}
	expectedTypes := []string{"hash", "hash", "zset", "zset", "set", "set", "hash"}
	for index, command := range types {
		if command.Val() != "none" && command.Val() != expectedTypes[index] {
			return false, queueAuthority("corruption", "preflight")
		}
	}
	routePipeline := q.client.Pipeline()
	fieldCount := routePipeline.HLen(ctx, q.keys[0])
	fieldValues := routePipeline.HMGet(ctx, q.keys[0], "shard_id", "engine_owner", "routing_epoch", "claim_sequence")
	if _, err := routePipeline.Exec(ctx); err != nil {
		return false, queueRedisFailure(err, "preflight")
	}
	values := fieldValues.Val()
	if fieldCount.Val() != 4 || len(values) != 4 {
		return false, queueAuthority("corruption", "preflight")
	}
	fields := make([]string, len(values))
	for index, raw := range values {
		value, ok := raw.(string)
		if !ok {
			return false, queueAuthority("corruption", "preflight")
		}
		fields[index] = value
	}
	if !safeID.MatchString(fields[0]) || !contains(set("python", "go"), fields[1]) {
		return false, queueAuthority("corruption", "preflight")
	}
	epoch, epochErr := strconv.ParseInt(fields[2], 10, 64)
	sequence, sequenceErr := strconv.ParseInt(fields[3], 10, 64)
	if epochErr != nil || epoch < 1 || epoch > maxInteger || strconv.FormatInt(epoch, 10) != fields[2] ||
		sequenceErr != nil || sequence < 0 || sequence > maxInteger || strconv.FormatInt(sequence, 10) != fields[3] {
		return false, queueAuthority("corruption", "preflight")
	}
	if fields[0] != q.route.ShardID || epoch != q.route.RoutingEpoch || fields[1] != q.route.EngineOwner {
		return false, queueAuthority("fenced", "preflight")
	}
	if err := q.validateProducerOwner(ctx, owner); err != nil {
		return false, err
	}
	if full {
		if err := q.audit(ctx); err != nil {
			return false, err
		}
		if err := q.validateProducerOwner(ctx, owner); err != nil {
			return false, err
		}
	}
	return false, nil
}

func (q *b0Queue) validateProducerOwner(ctx context.Context, owner producerOwnerIdentity) error {
	fields := []string{"schema", "namespace", "shard_id", "routing_epoch", "engine_owner", "cohort", "board_count"}
	for _, slug := range owner.BoardSlugs {
		fields = append(fields, "board_slug:"+slug)
	}
	pipeline := q.client.Pipeline()
	keyType := pipeline.Type(ctx, producerOwnerKey)
	fieldCount := pipeline.HLen(ctx, producerOwnerKey)
	values := pipeline.HMGet(ctx, producerOwnerKey, fields...)
	if _, err := pipeline.Exec(ctx); err != nil {
		return queueRedisFailure(err, "preflight")
	}
	if keyType.Val() != "hash" || fieldCount.Val() != int64(len(fields)) || len(values.Val()) != len(fields) {
		return queueAuthority("corruption", "preflight")
	}
	want := []string{
		producerOwnerV1, owner.Namespace, owner.Route.ShardID,
		strconv.FormatInt(owner.Route.RoutingEpoch, 10), owner.Route.EngineOwner,
		owner.Cohort, strconv.Itoa(len(owner.BoardSlugs)),
	}
	for range owner.BoardSlugs {
		want = append(want, "1")
	}
	for index, raw := range values.Val() {
		value, ok := raw.(string)
		if !ok || value != want[index] {
			return queueAuthority("corruption", "preflight")
		}
	}
	return nil
}

func (q *b0Queue) inspect(ctx context.Context, taskID string) (*storedTask, error) {
	if ctx == nil || !safeID.MatchString(taskID) {
		return nil, errors.New("invalid B0 inspect request")
	}
	routeType, err := q.client.Type(ctx, q.keys[0]).Result()
	if err != nil {
		return nil, queueRedisFailure(err, "inspect")
	}
	if routeType == "none" {
		pipeline := q.client.Pipeline()
		types := make([]*redis.StatusCmd, 0, len(q.keys)-1)
		for _, key := range q.keys[1:] {
			types = append(types, pipeline.Type(ctx, key))
		}
		if _, err := pipeline.Exec(ctx); err != nil {
			return nil, queueRedisFailure(err, "inspect")
		}
		for _, command := range types {
			if command.Val() != "none" {
				return nil, queueAuthority("corruption", "inspect")
			}
		}
		return nil, nil
	}
	if routeType != "hash" {
		return nil, queueAuthority("corruption", "inspect")
	}
	encoded, err := q.client.HGet(ctx, q.keys[1], taskID).Result()
	if errors.Is(err, redis.Nil) {
		return nil, nil
	}
	if err != nil {
		return nil, queueRedisFailure(err, "inspect")
	}
	var raw map[string]json.RawMessage
	decoder := json.NewDecoder(strings.NewReader(encoded))
	if decoder.Decode(&raw) != nil || len(raw) != 20 {
		return nil, queueAuthority("corruption", "inspect")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return nil, queueAuthority("corruption", "inspect")
	}
	for _, field := range []string{"task_id", "task_kind", "state", "shard_id", "routing_epoch", "engine_owner", "config_revision", "policy_key", "domain", "payload", "payload_sha256", "payload_sha1", "claim_token", "claim_sequence", "lease_until_ms", "ready_at_ms", "visible_at_ms", "failures", "pending_ready_at_ms", "pending_first_time"} {
		if _, ok := raw[field]; !ok {
			return nil, queueAuthority("corruption", "inspect")
		}
	}
	var record struct {
		TaskID, TaskKind, State, ShardID, EngineOwner, PolicyKey, Domain, Payload, PayloadSHA256, PayloadSHA1 string
		RoutingEpoch, ConfigRevision, Failures                                                                int64
	}
	if json.Unmarshal(raw["task_id"], &record.TaskID) != nil || json.Unmarshal(raw["task_kind"], &record.TaskKind) != nil ||
		json.Unmarshal(raw["state"], &record.State) != nil || json.Unmarshal(raw["shard_id"], &record.ShardID) != nil ||
		json.Unmarshal(raw["routing_epoch"], &record.RoutingEpoch) != nil || json.Unmarshal(raw["engine_owner"], &record.EngineOwner) != nil ||
		json.Unmarshal(raw["config_revision"], &record.ConfigRevision) != nil || json.Unmarshal(raw["policy_key"], &record.PolicyKey) != nil ||
		json.Unmarshal(raw["domain"], &record.Domain) != nil || json.Unmarshal(raw["payload"], &record.Payload) != nil ||
		json.Unmarshal(raw["payload_sha256"], &record.PayloadSHA256) != nil || json.Unmarshal(raw["payload_sha1"], &record.PayloadSHA1) != nil ||
		json.Unmarshal(raw["failures"], &record.Failures) != nil {
		return nil, queueAuthority("corruption", "inspect")
	}
	if record.TaskID != taskID || record.TaskKind != "scrape" || record.ShardID != q.route.ShardID || record.RoutingEpoch != q.route.RoutingEpoch ||
		record.EngineOwner != q.route.EngineOwner || record.PolicyKey != queuePolicyKey || record.ConfigRevision < 1 || record.ConfigRevision > maxInteger ||
		record.Failures < 0 || record.Failures > 100 || !contains(set("ready", "inflight", "dead", "terminal"), record.State) || !hex160.MatchString(record.PayloadSHA1) {
		return nil, queueAuthority("corruption", "inspect")
	}
	var pendingReadyAt *int64
	var pendingFirstTime *bool
	if string(raw["pending_ready_at_ms"]) != "null" {
		var value int64
		if json.Unmarshal(raw["pending_ready_at_ms"], &value) != nil || value < 0 || value > maxInteger {
			return nil, queueAuthority("corruption", "inspect")
		}
		pendingReadyAt = &value
	}
	if string(raw["pending_first_time"]) != "null" {
		var value bool
		if json.Unmarshal(raw["pending_first_time"], &value) != nil {
			return nil, queueAuthority("corruption", "inspect")
		}
		pendingFirstTime = &value
	}
	if (pendingReadyAt == nil) != (pendingFirstTime == nil) || (record.State != "inflight" && pendingReadyAt != nil) {
		return nil, queueAuthority("corruption", "inspect")
	}
	legacyDigest := sha1.Sum([]byte(record.Payload)) //nolint:gosec
	if hex.EncodeToString(legacyDigest[:]) != record.PayloadSHA1 {
		return nil, queueAuthority("corruption", "inspect")
	}
	task, err := decodeQueueTask(record.Payload, record.PayloadSHA256, q.route)
	if err != nil || task.Envelope.TaskID != taskID || task.Envelope.ConfigRevision != record.ConfigRevision || task.Envelope.PolicyKey != record.PolicyKey || task.Envelope.Domain != record.Domain {
		return nil, queueAuthority("corruption", "inspect")
	}
	return &storedTask{Task: task, State: record.State, Failures: record.Failures, PendingReadyAtMS: pendingReadyAt, PendingFirstTime: pendingFirstTime}, nil
}

func contains(values map[string]struct{}, value string) bool {
	_, ok := values[value]
	return ok
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
	return &lease{Task: task, ClaimToken: result.ClaimToken, LeaseUntilMS: result.LeaseUntilMS, DueAtMS: result.Value}, result, nil
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

func (q *b0Queue) release(ctx context.Context, current *lease, readyAtMS int64) error {
	result, err := q.callWithProducer(
		ctx, "reschedule_at", &current.Task, current.ClaimToken, 0, readyAtMS, 0,
		current.LeaseUntilMS, "", "", false, true, producerOwnerIdentity{},
	)
	if err != nil || !result.accepted() || result.ClaimToken != current.ClaimToken {
		return transitionError("shutdown-release", result, err)
	}
	return nil
}

func (q *b0Queue) fail(ctx context.Context, current *lease, readyAtMS int64) error {
	result, err := q.call(ctx, "fail_at", &current.Task, current.ClaimToken, 0, readyAtMS, 0, current.LeaseUntilMS)
	if err != nil || !result.accepted() || result.ClaimToken != current.ClaimToken {
		return transitionError("fail_at", result, err)
	}
	return nil
}

func (q *b0Queue) reap(ctx context.Context, maxFailures int64) error {
	result, err := q.call(ctx, "reap_expired", nil, "", 0, 0, maxFailures, 0)
	if err != nil || !result.accepted() {
		return transitionError("reap_expired", result, err)
	}
	q.metrics.reaped[0].Add(uint64(result.Value))
	q.metrics.reaped[1].Add(uint64(result.SecondaryValue))
	return nil
}

func (q *b0Queue) audit(ctx context.Context) error {
	result, err := q.call(ctx, "audit", nil, "", 0, 0, 0, 0)
	if err != nil || !result.accepted() {
		return transitionError("audit", result, err)
	}
	pipeline := q.client.Pipeline()
	ready := pipeline.ZCard(ctx, q.keys[2])
	inflight := pipeline.ZCard(ctx, q.keys[3])
	dead := pipeline.SCard(ctx, q.keys[4])
	if _, err := pipeline.Exec(ctx); err != nil {
		return queueRedisFailure(err, "audit")
	}
	q.metrics.readyCount.Store(ready.Val())
	q.metrics.redisInflight.Store(inflight.Val())
	q.metrics.deadCount.Store(dead.Val())
	return nil
}

func (q *b0Queue) lifetimeOccupancy(ctx context.Context) (int64, error) {
	if ctx == nil {
		return 0, queueAuthority("corruption", "capacity")
	}
	count, err := q.client.HLen(ctx, q.keys[1]).Result()
	if err != nil {
		return 0, queueRedisFailure(err, "capacity")
	}
	if count < 0 || count > queueRecordLimit {
		return 0, queueAuthority("corruption", "capacity")
	}
	return count, nil
}

func transitionError(operation string, result transition, err error) error {
	if err != nil {
		if errors.Is(err, context.Canceled) {
			return context.Canceled
		}
		var failure queueAuthorityError
		if errors.As(err, &failure) {
			return failure
		}
		return queueAuthority("corruption", operation)
	}
	if result.Decision == "fenced" {
		return queueAuthority("fenced", operation)
	}
	return queueAuthority("corruption", operation)
}
