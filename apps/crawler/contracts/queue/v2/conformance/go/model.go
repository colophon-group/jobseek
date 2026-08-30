// Package queuev2 is the inactive Go conformance implementation of the
// queue-protocol-v2 reference state machine. It has no production Redis or
// database integration.
package queuev2

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"sort"
)

const Format = "jobseek.queue.v2.conformance/v1"

type Corpus struct {
	Cases  []Case `json:"cases"`
	Format string `json:"format"`
}

type Case struct {
	Expected     Outcome     `json:"expected"`
	ID           string      `json:"id"`
	Initial      Snapshot    `json:"initial"`
	Operations   []Operation `json:"operations"`
	ResultDigest string      `json:"result_digest"`
}

type Route struct {
	EngineOwner  string `json:"engine_owner"`
	RoutingEpoch int64  `json:"routing_epoch"`
	ShardID      string `json:"shard_id"`
}

type Fence struct {
	ClaimToken     string `json:"claim_token"`
	ConfigRevision int64  `json:"config_revision"`
	EngineOwner    string `json:"engine_owner"`
	RoutingEpoch   int64  `json:"routing_epoch"`
	ShardID        string `json:"shard_id"`
}

type Record struct {
	ClaimToken     *string `json:"claim_token"`
	ConfigRevision int64   `json:"config_revision"`
	EngineOwner    string  `json:"engine_owner"`
	Failures       int64   `json:"failures"`
	LeaseUntil     *int64  `json:"lease_until"`
	RoutingEpoch   int64   `json:"routing_epoch"`
	ShardID        string  `json:"shard_id"`
	State          string  `json:"state"`
	TaskID         string  `json:"task_id"`
}

type Snapshot struct {
	Configs      map[string]int64 `json:"configs"`
	IssuedTokens []string         `json:"issued_tokens"`
	Records      []Record         `json:"records"`
	Route        Route            `json:"route"`
}

type Operation struct {
	Fence       *Fence `json:"fence"`
	Kind        string `json:"kind"`
	LeaseUntil  *int64 `json:"lease_until,omitempty"`
	MaxFailures *int64 `json:"max_failures,omitempty"`
	Now         *int64 `json:"now,omitempty"`
	TaskID      string `json:"task_id"`
}

type Violation struct {
	Code   string `json:"code"`
	Detail string `json:"detail"`
	TaskID string `json:"task_id"`
}

type Audit struct {
	OK         bool        `json:"ok"`
	Violations []Violation `json:"violations"`
}

type TraceEntry struct {
	Decision        string `json:"decision"`
	Index           int    `json:"index"`
	Kind            string `json:"kind"`
	Reason          string `json:"reason"`
	SnapshotDigest  string `json:"snapshot_digest"`
	WriteAuthorized bool   `json:"write_authorized"`
}

type Outcome struct {
	Audit Audit        `json:"audit"`
	Final Snapshot     `json:"final"`
	Trace []TraceEntry `json:"trace"`
}

func DecodeCorpus(data []byte) (Corpus, error) {
	var corpus Corpus
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&corpus); err != nil {
		return Corpus{}, err
	}
	if err := ensureEOF(decoder); err != nil {
		return Corpus{}, err
	}
	if corpus.Format != Format {
		return Corpus{}, fmt.Errorf("unsupported corpus format %q", corpus.Format)
	}
	for index := range corpus.Cases {
		if err := validateCase(corpus.Cases[index]); err != nil {
			return Corpus{}, fmt.Errorf("case %d: %w", index, err)
		}
	}
	return corpus, nil
}

func ensureEOF(decoder *json.Decoder) error {
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return fmt.Errorf("unexpected trailing JSON value")
		}
		return err
	}
	return nil
}

func validOwner(owner string) bool {
	return owner == "python" || owner == "go"
}

func validState(state string) bool {
	switch state {
	case "ready", "inflight", "dead_letter", "terminal":
		return true
	default:
		return false
	}
}

func validateRoute(route Route) error {
	if route.ShardID == "" || route.RoutingEpoch < 1 || !validOwner(route.EngineOwner) {
		return fmt.Errorf("invalid route")
	}
	return nil
}

func validateFence(fence *Fence) error {
	if fence == nil {
		return fmt.Errorf("missing fence")
	}
	if fence.ShardID == "" || fence.RoutingEpoch < 1 || !validOwner(fence.EngineOwner) ||
		fence.ConfigRevision < 1 || fence.ClaimToken == "" {
		return fmt.Errorf("invalid fence")
	}
	return nil
}

func validateSnapshot(snapshot Snapshot) error {
	if err := validateRoute(snapshot.Route); err != nil {
		return err
	}
	if snapshot.Configs == nil || snapshot.IssuedTokens == nil || snapshot.Records == nil {
		return fmt.Errorf("configs, issued_tokens, and records must be present")
	}
	for taskID, revision := range snapshot.Configs {
		if taskID == "" || revision < 1 {
			return fmt.Errorf("invalid config entry")
		}
	}
	for _, token := range snapshot.IssuedTokens {
		if token == "" {
			return fmt.Errorf("empty issued token")
		}
	}
	for _, record := range snapshot.Records {
		if record.TaskID == "" || !validState(record.State) || record.ShardID == "" ||
			record.RoutingEpoch < 1 || !validOwner(record.EngineOwner) ||
			record.ConfigRevision < 1 || record.Failures < 0 {
			return fmt.Errorf("invalid lifecycle record for %q", record.TaskID)
		}
		if record.ClaimToken != nil && *record.ClaimToken == "" {
			return fmt.Errorf("empty claim token for %q", record.TaskID)
		}
		if record.LeaseUntil != nil && *record.LeaseUntil < 0 {
			return fmt.Errorf("negative lease for %q", record.TaskID)
		}
	}
	return nil
}

func validateOperation(operation Operation) error {
	if operation.TaskID == "" {
		return fmt.Errorf("missing task_id")
	}
	if err := validateFence(operation.Fence); err != nil {
		return err
	}
	switch operation.Kind {
	case "claim", "heartbeat":
		if operation.LeaseUntil == nil || *operation.LeaseUntil < 0 ||
			operation.MaxFailures != nil || operation.Now != nil {
			return fmt.Errorf("invalid %s fields", operation.Kind)
		}
	case "authorize_write", "complete", "reschedule":
		if operation.LeaseUntil != nil || operation.MaxFailures != nil || operation.Now != nil {
			return fmt.Errorf("invalid %s fields", operation.Kind)
		}
	case "reap":
		if operation.Now == nil || *operation.Now < 0 || operation.MaxFailures == nil ||
			*operation.MaxFailures < 1 || operation.LeaseUntil != nil {
			return fmt.Errorf("invalid reap fields")
		}
	case "fail":
		if operation.MaxFailures == nil || *operation.MaxFailures < 1 ||
			operation.LeaseUntil != nil || operation.Now != nil {
			return fmt.Errorf("invalid fail fields")
		}
	default:
		return fmt.Errorf("unsupported operation %q", operation.Kind)
	}
	return nil
}

func validateCase(testCase Case) error {
	if testCase.ID == "" {
		return fmt.Errorf("missing case id")
	}
	if err := validateSnapshot(testCase.Initial); err != nil {
		return err
	}
	if testCase.Operations == nil {
		return fmt.Errorf("operations must be present")
	}
	for _, operation := range testCase.Operations {
		if err := validateOperation(operation); err != nil {
			return err
		}
	}
	return nil
}

func copyString(value *string) *string {
	if value == nil {
		return nil
	}
	copied := *value
	return &copied
}

func copyInt64(value *int64) *int64 {
	if value == nil {
		return nil
	}
	copied := *value
	return &copied
}

func cloneSnapshot(snapshot Snapshot) Snapshot {
	cloned := Snapshot{
		Configs:      make(map[string]int64, len(snapshot.Configs)),
		IssuedTokens: make([]string, len(snapshot.IssuedTokens)),
		Records:      make([]Record, len(snapshot.Records)),
		Route:        snapshot.Route,
	}
	for taskID, revision := range snapshot.Configs {
		cloned.Configs[taskID] = revision
	}
	copy(cloned.Records, snapshot.Records)
	copy(cloned.IssuedTokens, snapshot.IssuedTokens)
	for index := range cloned.Records {
		cloned.Records[index].ClaimToken = copyString(snapshot.Records[index].ClaimToken)
		cloned.Records[index].LeaseUntil = copyInt64(snapshot.Records[index].LeaseUntil)
	}
	return cloned
}

func pointerString(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

func NormalizeSnapshot(snapshot Snapshot) Snapshot {
	normalized := cloneSnapshot(snapshot)
	sort.Strings(normalized.IssuedTokens)
	sort.Slice(normalized.Records, func(left, right int) bool {
		a := normalized.Records[left]
		b := normalized.Records[right]
		if a.TaskID != b.TaskID {
			return a.TaskID < b.TaskID
		}
		if a.State != b.State {
			return a.State < b.State
		}
		if pointerString(a.ClaimToken) != pointerString(b.ClaimToken) {
			return pointerString(a.ClaimToken) < pointerString(b.ClaimToken)
		}
		if a.ConfigRevision != b.ConfigRevision {
			return a.ConfigRevision < b.ConfigRevision
		}
		return a.RoutingEpoch < b.RoutingEpoch
	})
	return normalized
}

func AuditSnapshot(snapshot Snapshot) (Audit, error) {
	if err := validateSnapshot(snapshot); err != nil {
		return Audit{}, err
	}
	counts := make(map[string]int)
	tokenCounts := make(map[string]int)
	issuedCounts := make(map[string]int)
	for _, token := range snapshot.IssuedTokens {
		issuedCounts[token]++
	}
	for _, record := range snapshot.Records {
		counts[record.TaskID]++
		if record.State == "inflight" && record.ClaimToken != nil {
			tokenCounts[*record.ClaimToken]++
		}
	}
	violations := make([]Violation, 0)
	add := func(code, taskID, detail string) {
		violations = append(violations, Violation{Code: code, Detail: detail, TaskID: taskID})
	}
	issuedTokens := make([]string, 0, len(issuedCounts))
	for token := range issuedCounts {
		issuedTokens = append(issuedTokens, token)
	}
	sort.Strings(issuedTokens)
	for _, token := range issuedTokens {
		if issuedCounts[token] > 1 {
			add("issued_token_duplication", token, "issued-token ledger contains a duplicate")
		}
	}
	configIDs := make([]string, 0, len(snapshot.Configs))
	for taskID := range snapshot.Configs {
		configIDs = append(configIDs, taskID)
	}
	sort.Strings(configIDs)
	for _, taskID := range configIDs {
		switch {
		case counts[taskID] == 0:
			add("loss", taskID, "configured task has no lifecycle record")
		case counts[taskID] > 1:
			add("duplication", taskID, "task occupies multiple lifecycle records")
		}
	}
	for _, record := range snapshot.Records {
		revision, configured := snapshot.Configs[record.TaskID]
		if !configured {
			add("orphan_config", record.TaskID, "lifecycle record has no configuration")
		} else if record.ConfigRevision != revision {
			add("config_revision_mismatch", record.TaskID, "record revision differs from configured revision")
		}
		if record.ShardID != snapshot.Route.ShardID {
			add("shard_mismatch", record.TaskID, "record shard differs from active route")
		}
		if record.RoutingEpoch != snapshot.Route.RoutingEpoch {
			add("routing_epoch_mismatch", record.TaskID, "record epoch differs from active route")
		}
		if record.EngineOwner != snapshot.Route.EngineOwner {
			add("engine_owner_mismatch", record.TaskID, "record owner differs from active route")
		}
		if record.State == "inflight" {
			if record.ClaimToken == nil || record.LeaseUntil == nil {
				add("invalid_inflight", record.TaskID, "inflight record lacks token or lease")
			} else if issuedCounts[*record.ClaimToken] == 0 {
				add("unregistered_claim_token", record.TaskID, "inflight token is absent from issued-token ledger")
			} else if tokenCounts[*record.ClaimToken] > 1 {
				add("token_collision", record.TaskID, "claim token is reused by another task")
			}
		} else if record.ClaimToken != nil || record.LeaseUntil != nil {
			add("invalid_non_inflight", record.TaskID, "non-inflight record retains token or lease")
		}
	}
	sort.Slice(violations, func(left, right int) bool {
		a := violations[left]
		b := violations[right]
		if a.Code != b.Code {
			return a.Code < b.Code
		}
		if a.TaskID != b.TaskID {
			return a.TaskID < b.TaskID
		}
		return a.Detail < b.Detail
	})
	return Audit{OK: len(violations) == 0, Violations: violations}, nil
}

func recordIndexes(snapshot Snapshot, taskID string) []int {
	indexes := make([]int, 0, 1)
	for index := range snapshot.Records {
		if snapshot.Records[index].TaskID == taskID {
			indexes = append(indexes, index)
		}
	}
	return indexes
}

func fenceReason(snapshot Snapshot, record Record, fence Fence, claim bool) string {
	if fence.ShardID != snapshot.Route.ShardID || fence.RoutingEpoch != snapshot.Route.RoutingEpoch ||
		fence.EngineOwner != snapshot.Route.EngineOwner {
		return "route_mismatch"
	}
	configuredRevision, ok := snapshot.Configs[record.TaskID]
	if !ok {
		return "config_missing"
	}
	if fence.ConfigRevision != configuredRevision {
		return "config_revision_mismatch"
	}
	if record.ShardID != fence.ShardID || record.RoutingEpoch != fence.RoutingEpoch ||
		record.EngineOwner != fence.EngineOwner || record.ConfigRevision != fence.ConfigRevision {
		return "record_fence_mismatch"
	}
	if !claim && (record.ClaimToken == nil || *record.ClaimToken != fence.ClaimToken) {
		return "claim_mismatch"
	}
	return ""
}

func setPartition(record *Record, state string) {
	record.State = state
	record.ClaimToken = nil
	record.LeaseUntil = nil
}

func apply(snapshot *Snapshot, operation Operation) (string, string, bool) {
	indexes := recordIndexes(*snapshot, operation.TaskID)
	if len(indexes) != 1 {
		return "rejected", "state_not_unique", false
	}
	record := &snapshot.Records[indexes[0]]
	fence := *operation.Fence
	if operation.Kind == "claim" {
		if record.State != "ready" {
			return "rejected", "not_ready", false
		}
		if reason := fenceReason(*snapshot, *record, fence, true); reason != "" {
			return "fenced", reason, false
		}
		for _, issued := range snapshot.IssuedTokens {
			if issued == fence.ClaimToken {
				return "rejected", "claim_token_reused", false
			}
		}
		record.State = "inflight"
		record.ClaimToken = copyString(&fence.ClaimToken)
		record.LeaseUntil = copyInt64(operation.LeaseUntil)
		snapshot.IssuedTokens = append(snapshot.IssuedTokens, fence.ClaimToken)
		return "accepted", "claimed", false
	}
	if record.State != "inflight" {
		return "rejected", "not_inflight", false
	}
	if reason := fenceReason(*snapshot, *record, fence, false); reason != "" {
		return "fenced", reason, false
	}
	switch operation.Kind {
	case "heartbeat":
		if record.LeaseUntil == nil || *operation.LeaseUntil <= *record.LeaseUntil {
			return "rejected", "lease_not_extended", false
		}
		record.LeaseUntil = copyInt64(operation.LeaseUntil)
		return "accepted", "lease_extended", false
	case "authorize_write":
		return "accepted", "write_authorized", true
	case "complete":
		setPartition(record, "terminal")
		return "accepted", "completed", false
	case "reschedule":
		setPartition(record, "ready")
		return "accepted", "rescheduled", false
	case "reap":
		if record.LeaseUntil == nil {
			return "rejected", "invalid_record", false
		}
		if *operation.Now < *record.LeaseUntil {
			return "rejected", "lease_not_expired", false
		}
		fallthrough
	case "fail":
		record.Failures++
		if record.Failures >= *operation.MaxFailures {
			setPartition(record, "dead_letter")
			return "accepted", "dead_lettered", false
		}
		setPartition(record, "ready")
		return "accepted", "requeued", false
	default:
		panic("validated operation not implemented")
	}
}

func CanonicalBytes(value any) ([]byte, error) {
	return json.Marshal(value)
}

func Digest(value any) (string, error) {
	encoded, err := CanonicalBytes(value)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(encoded)
	return hex.EncodeToString(sum[:]), nil
}

func RunCase(testCase Case) (Outcome, error) {
	if err := validateCase(testCase); err != nil {
		return Outcome{}, err
	}
	snapshot := cloneSnapshot(testCase.Initial)
	trace := make([]TraceEntry, 0, len(testCase.Operations))
	for index, operation := range testCase.Operations {
		decision, reason, writeAuthorized := apply(&snapshot, operation)
		normalized := NormalizeSnapshot(snapshot)
		snapshotDigest, err := Digest(normalized)
		if err != nil {
			return Outcome{}, err
		}
		trace = append(trace, TraceEntry{
			Decision:        decision,
			Index:           index,
			Kind:            operation.Kind,
			Reason:          reason,
			SnapshotDigest:  snapshotDigest,
			WriteAuthorized: writeAuthorized,
		})
	}
	final := NormalizeSnapshot(snapshot)
	audit, err := AuditSnapshot(final)
	if err != nil {
		return Outcome{}, err
	}
	return Outcome{Audit: audit, Final: final, Trace: trace}, nil
}
