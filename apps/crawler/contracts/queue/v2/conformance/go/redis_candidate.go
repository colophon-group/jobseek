package queuev2

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/redis/go-redis/v9"
)

const RedisCandidateMaxInteger int64 = 9_999_999_999_999

type RedisDecision string

const (
	RedisAccepted       RedisDecision = "accepted"
	RedisFenced         RedisDecision = "fenced"
	RedisNotCurrent     RedisDecision = "not_current"
	RedisTransportError RedisDecision = "transport-error"
)

var (
	candidateNamespacePattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$`)
	canonicalUintPattern      = regexp.MustCompile(`^(0|[1-9][0-9]*)$`)
	claimTokenPattern         = regexp.MustCompile(`^([1-9][0-9]*):([1-9][0-9]*)$`)
	operations                = map[string]bool{
		"initialize": true, "register": true, "claim": true, "heartbeat": true,
		"complete": true, "reschedule": true, "reap": true,
	}
	acceptedReasons = map[string]map[string]bool{
		"initialize": {"initialized": true, "already_initialized": true},
		"register":   {"registered": true},
		"claim":      {"claimed": true},
		"heartbeat":  {"lease_extended": true},
		"complete":   {"completed": true},
		"reschedule": {"rescheduled": true},
		"reap":       {"requeued": true, "dead_lettered": true},
	}
	notCurrentReasons = map[string]map[string]bool{
		"initialize": set("redis_time_invalid", "invalid_route", "namespace_corrupt"),
		"register": set(
			"redis_time_invalid", "invalid_route", "invalid_task_identity",
			"namespace_corrupt", "task_already_exists",
		),
		"claim": set(
			"redis_time_invalid", "invalid_route", "invalid_task_identity",
			"namespace_corrupt", "config_missing", "record_missing", "record_corrupt",
			"conservation_violation", "state_mismatch", "not_due", "invalid_lease_ttl",
			"numeric_overflow", "claim_sequence_exhausted", "claim_sequence_corrupt",
		),
		"heartbeat": set(
			"redis_time_invalid", "invalid_route", "invalid_task_identity",
			"namespace_corrupt", "config_missing", "record_missing", "record_corrupt",
			"conservation_violation", "state_mismatch", "lease_expired",
			"invalid_lease_ttl", "numeric_overflow", "lease_not_extended",
		),
		"complete": set(
			"redis_time_invalid", "invalid_route", "invalid_task_identity",
			"namespace_corrupt", "config_missing", "record_missing", "record_corrupt",
			"conservation_violation", "state_mismatch", "lease_expired",
		),
		"reschedule": set(
			"redis_time_invalid", "invalid_route", "invalid_task_identity",
			"namespace_corrupt", "config_missing", "record_missing", "record_corrupt",
			"conservation_violation", "state_mismatch", "lease_expired",
			"invalid_reschedule_delay", "numeric_overflow",
		),
		"reap": set(
			"redis_time_invalid", "invalid_route", "invalid_task_identity",
			"namespace_corrupt", "config_missing", "record_missing", "record_corrupt",
			"conservation_violation", "state_mismatch", "invalid_max_failures",
			"lease_active", "numeric_overflow",
		),
	}
)

func set(values ...string) map[string]bool {
	result := make(map[string]bool, len(values))
	for _, value := range values {
		result[value] = true
	}
	return result
}

func legalFenceReason(operation, reason string) bool {
	switch reason {
	case "shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch":
		return true
	case "config_revision_mismatch", "record_fence_mismatch":
		return operation != "initialize" && operation != "register"
	case "claim_token_mismatch":
		return operation == "heartbeat" || operation == "complete" ||
			operation == "reschedule" || operation == "reap"
	default:
		return false
	}
}

type RedisTransition struct {
	Decision     RedisDecision
	Reason       string
	ServerTimeMS int64
	ClaimToken   string
	Value        int64
	HasValue     bool
}

type RedisCandidateOperation struct {
	Kind               string
	TaskID             string
	Route              Route
	ConfigRevision     int64
	ClaimToken         string
	LeaseTTLMS         int64
	RescheduleDelayMS  int64
	MaxFailures        int64
	PreviousLeaseUntil int64
}

type RedisCandidateClient struct {
	client redis.Scripter
	keys   []string
	script *redis.Script
}

func NewRedisCandidateClient(
	client redis.Scripter,
	namespace string,
	luaSource string,
) (*RedisCandidateClient, error) {
	if !candidateNamespacePattern.MatchString(namespace) {
		return nil, &CandidateValidationError{"invalid candidate Redis namespace"}
	}
	if luaSource == "" {
		return nil, &CandidateValidationError{"candidate Lua source is empty"}
	}
	tag := "queue-v2-candidate:{" + namespace + "}"
	return &RedisCandidateClient{
		client: client,
		keys: []string{
			tag + ":route",
			tag + ":configs",
			tag + ":records",
			tag + ":ready",
			tag + ":inflight",
			tag + ":dead-letter",
			tag + ":terminal",
		},
		script: redis.NewScript(luaSource),
	}, nil
}

type CandidateValidationError struct{ message string }

func (e *CandidateValidationError) Error() string { return e.message }

func (c *RedisCandidateClient) Keys() []string { return append([]string(nil), c.keys...) }

func (c *RedisCandidateClient) Execute(
	ctx context.Context,
	operation RedisCandidateOperation,
) (RedisTransition, error) {
	if err := validateRedisOperation(operation); err != nil {
		return RedisTransition{}, err
	}
	args := []any{
		operation.Kind,
		operation.TaskID,
		operation.Route.ShardID,
		strconv.FormatInt(operation.Route.RoutingEpoch, 10),
		operation.Route.EngineOwner,
		strconv.FormatInt(operation.ConfigRevision, 10),
		operation.ClaimToken,
		strconv.FormatInt(operation.LeaseTTLMS, 10),
		strconv.FormatInt(operation.RescheduleDelayMS, 10),
		strconv.FormatInt(operation.MaxFailures, 10),
	}
	raw, err := c.script.Run(ctx, c.client, c.keys, args...).Slice()
	if err != nil {
		return RedisTransition{Decision: RedisTransportError, Reason: "redis_error"}, nil
	}
	return decodeRedisCandidateReply(operation, raw), nil
}

func validateRedisOperation(operation RedisCandidateOperation) error {
	if !operations[operation.Kind] {
		return &CandidateValidationError{"unsupported operation"}
	}
	if operation.Route.ShardID == "" ||
		(operation.Route.EngineOwner != "python" && operation.Route.EngineOwner != "go") {
		return &CandidateValidationError{"invalid route"}
	}
	if err := boundedInt(operation.Route.RoutingEpoch, 1, "routing epoch"); err != nil {
		return err
	}
	if operation.Kind == "initialize" {
		if operation.TaskID != "" || operation.ConfigRevision != 0 || operation.ClaimToken != "" ||
			operation.LeaseTTLMS != 0 || operation.RescheduleDelayMS != 0 || operation.MaxFailures != 0 {
			return &CandidateValidationError{"initialize has unrelated fields"}
		}
		return nil
	}
	if operation.TaskID == "" {
		return &CandidateValidationError{"empty task id"}
	}
	if err := boundedInt(operation.ConfigRevision, 1, "config revision"); err != nil {
		return err
	}
	switch operation.Kind {
	case "register":
		if operation.ClaimToken != "" || operation.LeaseTTLMS != 0 ||
			operation.RescheduleDelayMS != 0 || operation.MaxFailures != 0 {
			return &CandidateValidationError{"register has unrelated fields"}
		}
	case "claim":
		if operation.ClaimToken != "" || operation.RescheduleDelayMS != 0 ||
			operation.MaxFailures != 0 {
			return &CandidateValidationError{"claim has unrelated fields"}
		}
		if err := boundedInt(operation.LeaseTTLMS, 1, "lease TTL"); err != nil {
			return err
		}
	case "heartbeat":
		if err := validateClaimToken(operation.ClaimToken, operation.Route.RoutingEpoch); err != nil {
			return err
		}
		if err := boundedInt(operation.LeaseTTLMS, 1, "lease TTL"); err != nil {
			return err
		}
		if err := boundedInt(operation.PreviousLeaseUntil, 1, "previous lease"); err != nil {
			return err
		}
		if operation.RescheduleDelayMS != 0 || operation.MaxFailures != 0 {
			return &CandidateValidationError{"heartbeat has unrelated fields"}
		}
	case "complete":
		if err := validateClaimToken(operation.ClaimToken, operation.Route.RoutingEpoch); err != nil {
			return err
		}
		if operation.LeaseTTLMS != 0 || operation.RescheduleDelayMS != 0 || operation.MaxFailures != 0 {
			return &CandidateValidationError{"complete has unrelated fields"}
		}
	case "reschedule":
		if err := validateClaimToken(operation.ClaimToken, operation.Route.RoutingEpoch); err != nil {
			return err
		}
		if err := boundedInt(operation.RescheduleDelayMS, 0, "reschedule delay"); err != nil {
			return err
		}
		if operation.LeaseTTLMS != 0 || operation.MaxFailures != 0 {
			return &CandidateValidationError{"reschedule has unrelated fields"}
		}
	case "reap":
		if err := validateClaimToken(operation.ClaimToken, operation.Route.RoutingEpoch); err != nil {
			return err
		}
		if err := boundedInt(operation.MaxFailures, 1, "max failures"); err != nil {
			return err
		}
		if operation.LeaseTTLMS != 0 || operation.RescheduleDelayMS != 0 {
			return &CandidateValidationError{"reap has unrelated fields"}
		}
	}
	return nil
}

func boundedInt(value, minimum int64, name string) error {
	if value < minimum || value > RedisCandidateMaxInteger {
		return &CandidateValidationError{fmt.Sprintf("%s outside canonical domain", name)}
	}
	return nil
}

func canonicalUint(text string, minimum int64) (int64, error) {
	if !canonicalUintPattern.MatchString(text) {
		return 0, &CandidateValidationError{"non-canonical wire integer"}
	}
	value, err := strconv.ParseInt(text, 10, 64)
	if err != nil || boundedInt(value, minimum, "wire integer") != nil {
		return 0, &CandidateValidationError{"wire integer outside canonical domain"}
	}
	return value, nil
}

func validateClaimToken(token string, expectedEpoch int64) error {
	parts := claimTokenPattern.FindStringSubmatch(token)
	if parts == nil {
		return &CandidateValidationError{"invalid claim token"}
	}
	epoch, err := canonicalUint(parts[1], 1)
	if err != nil || epoch != expectedEpoch {
		return &CandidateValidationError{"claim token epoch mismatch"}
	}
	_, err = canonicalUint(parts[2], 1)
	return err
}

func invalidRedisReply() RedisTransition {
	return RedisTransition{Decision: RedisTransportError, Reason: "invalid_redis_reply"}
}

func decodeRedisCandidateReply(operation RedisCandidateOperation, raw []any) RedisTransition {
	if len(raw) != 5 || !operations[operation.Kind] {
		return invalidRedisReply()
	}
	values := make([]string, len(raw))
	for index, value := range raw {
		text, ok := value.(string)
		if !ok {
			return invalidRedisReply()
		}
		values[index] = text
	}
	decision := RedisDecision(values[0])
	if decision == RedisTransportError ||
		(decision != RedisAccepted && decision != RedisFenced && decision != RedisNotCurrent) {
		return invalidRedisReply()
	}
	serverTime, err := canonicalUint(values[2], 0)
	if err != nil || values[1] == "" || (values[1] != "redis_time_invalid" && serverTime == 0) {
		return invalidRedisReply()
	}
	transition := RedisTransition{
		Decision: decision, Reason: values[1], ServerTimeMS: serverTime, ClaimToken: values[3],
	}
	if values[4] != "" {
		transition.Value, err = canonicalUint(values[4], 0)
		if err != nil {
			return invalidRedisReply()
		}
		transition.HasValue = true
	}

	valid := false
	switch decision {
	case RedisAccepted:
		if !acceptedReasons[operation.Kind][transition.Reason] {
			return invalidRedisReply()
		}
		switch operation.Kind {
		case "initialize":
			valid = transition.ClaimToken == "" && !transition.HasValue
		case "register":
			valid = transition.ClaimToken == "" && transition.HasValue && transition.Value == serverTime
		case "claim":
			valid = validateClaimToken(transition.ClaimToken, operation.Route.RoutingEpoch) == nil &&
				transition.HasValue && transition.Value > serverTime
		case "heartbeat":
			valid = transition.ClaimToken == operation.ClaimToken && transition.HasValue &&
				transition.Value > serverTime && transition.Value > operation.PreviousLeaseUntil
		case "complete":
			valid = transition.ClaimToken == operation.ClaimToken && !transition.HasValue
		case "reschedule":
			valid = transition.ClaimToken == operation.ClaimToken && transition.HasValue &&
				transition.Value >= serverTime
		case "reap":
			valid = transition.ClaimToken == operation.ClaimToken && transition.HasValue && transition.Value >= 1
		}
	case RedisFenced:
		valid = legalFenceReason(operation.Kind, transition.Reason) &&
			transition.ClaimToken == "" && !transition.HasValue
	case RedisNotCurrent:
		if !notCurrentReasons[operation.Kind][transition.Reason] {
			return invalidRedisReply()
		}
		switch transition.Reason {
		case "redis_time_invalid":
			valid = serverTime == 0 && transition.ClaimToken == "" && !transition.HasValue
		case "not_due":
			valid = operation.Kind == "claim" && transition.ClaimToken == "" &&
				transition.HasValue && transition.Value > serverTime
		case "lease_active":
			valid = operation.Kind == "reap" && transition.ClaimToken == operation.ClaimToken &&
				transition.HasValue && transition.Value > serverTime
		default:
			valid = transition.ClaimToken == "" && !transition.HasValue
		}
	}
	if !valid {
		return invalidRedisReply()
	}
	return transition
}

func parseTokenAlias(value string) (string, bool) {
	return strings.CutPrefix(value, "$")
}
