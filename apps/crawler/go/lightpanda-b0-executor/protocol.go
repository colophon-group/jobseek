package executor

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"unicode/utf8"

	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/proto"
)

const (
	Protocol    = "jobseek.lightpanda.executor/v1"
	FrameLimit  = uint64(3 * 1024 * 1024)
	ResultLimit = 2 * 1024 * 1024
)

var ErrProtocol = errors.New("executor protocol rejected")

// Request validates the transport envelope only. The caller must also validate
// the canonical Go-owned task and route before asking for write authorization.
type Request struct {
	Task          b0task.Task
	TaskPayload   string
	PayloadSHA256 string
	ClaimToken    string
	LeaseUntilMS  int64
	Result        *runtimev1.BrowserResult
}

func (r *Request) validateTask(shard string, epoch int64) error {
	task, err := b0task.DecodeCanonical(r.TaskPayload, r.PayloadSHA256, b0task.Route{ShardID: shard, RoutingEpoch: epoch, EngineOwner: "go"})
	if err != nil {
		return ErrProtocol
	}
	fence := Fence{PostingID: task.Envelope.TaskID, ShardID: shard, RoutingEpoch: epoch, ConfigRevision: task.Envelope.ConfigRevision, PayloadSHA256: r.PayloadSHA256, ClaimToken: r.ClaimToken}
	if err := fence.Validate(); err != nil {
		return ErrProtocol
	}
	r.Task = task
	return nil
}

func ReadFrame(input io.Reader) ([]byte, error) {
	return framing.ReadRecord(input, FrameLimit)
}

func WriteMessage(output io.Writer, message map[string]any) error {
	payload, err := json.Marshal(message)
	if err != nil {
		return ErrProtocol
	}
	record, err := framing.EncodeRecord(payload, FrameLimit)
	if err != nil {
		return err
	}
	for len(record) != 0 {
		n, err := output.Write(record)
		if n < 0 || n > len(record) {
			return io.ErrShortWrite
		}
		if err != nil {
			return err
		}
		if n == 0 {
			return io.ErrShortWrite
		}
		record = record[n:]
	}
	return nil
}

func exactObject(payload []byte, names ...string) (map[string]json.RawMessage, error) {
	if !utf8.Valid(payload) || uint64(len(payload)) >= FrameLimit {
		return nil, ErrProtocol
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	var fields map[string]json.RawMessage
	if err := decoder.Decode(&fields); err != nil || fields == nil || len(fields) != len(names) {
		return nil, ErrProtocol
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return nil, ErrProtocol
	}
	for _, name := range names {
		if _, ok := fields[name]; !ok {
			return nil, ErrProtocol
		}
	}
	return fields, nil
}

func stringField(raw json.RawMessage) (string, error) {
	var value string
	if bytes.Equal(raw, []byte("null")) || json.Unmarshal(raw, &value) != nil {
		return "", ErrProtocol
	}
	return value, nil
}

func integerField(raw json.RawMessage) (int64, error) {
	var value int64
	if bytes.Equal(raw, []byte("null")) || json.Unmarshal(raw, &value) != nil {
		return 0, ErrProtocol
	}
	return value, nil
}

func DecodeRequest(payload []byte) (Request, error) {
	fields, err := exactObject(payload, "version", "task_payload", "payload_sha256", "claim_token", "lease_until_ms", "browser_result")
	if err != nil {
		return Request{}, err
	}
	version, err := stringField(fields["version"])
	if err != nil || version != Protocol {
		return Request{}, ErrProtocol
	}
	var request Request
	if request.TaskPayload, err = stringField(fields["task_payload"]); err != nil {
		return Request{}, err
	}
	if request.PayloadSHA256, err = stringField(fields["payload_sha256"]); err != nil {
		return Request{}, err
	}
	digest := sha256.Sum256([]byte(request.TaskPayload))
	if !hexDigest.MatchString(request.PayloadSHA256) || hex.EncodeToString(digest[:]) != request.PayloadSHA256 {
		return Request{}, ErrProtocol
	}
	if request.ClaimToken, err = stringField(fields["claim_token"]); err != nil {
		return Request{}, err
	}
	if request.LeaseUntilMS, err = integerField(fields["lease_until_ms"]); err != nil || request.LeaseUntilMS < 1 || request.LeaseUntilMS > maxIdentityInteger {
		return Request{}, ErrProtocol
	}
	encoded, err := stringField(fields["browser_result"])
	if err != nil || len(encoded) > base64.StdEncoding.EncodedLen(ResultLimit) {
		return Request{}, ErrProtocol
	}
	result, err := base64.StdEncoding.Strict().DecodeString(encoded)
	if err != nil || base64.StdEncoding.EncodeToString(result) != encoded {
		return Request{}, ErrProtocol
	}
	request.Result, err = DecodeResult(result)
	if err != nil {
		return Request{}, err
	}
	return request, nil
}

// DecodeResult rejects unknown fields recursively and noncanonical protobuf,
// matching the existing Python executor's discard-and-reserialize proof.
func DecodeResult(payload []byte) (*runtimev1.BrowserResult, error) {
	if len(payload) == 0 || len(payload) > ResultLimit {
		return nil, ErrProtocol
	}
	result := &runtimev1.BrowserResult{}
	if err := (proto.UnmarshalOptions{DiscardUnknown: true}).Unmarshal(payload, result); err != nil {
		return nil, ErrProtocol
	}
	canonical, err := (proto.MarshalOptions{Deterministic: true}).Marshal(result)
	if err != nil || !bytes.Equal(canonical, payload) || result.ContractVersion != "crawler.runtime/v1" || result.Backend != runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA {
		return nil, ErrProtocol
	}
	switch result.Outcome.(type) {
	case *runtimev1.BrowserResult_Success, *runtimev1.BrowserResult_Error, *runtimev1.BrowserResult_Unsupported:
		return result, nil
	default:
		return nil, ErrProtocol
	}
}

func DecodeAuthorization(payload []byte, claim string, previousLease int64) (int64, error) {
	fields, err := exactObject(payload, "type", "claim_token", "lease_until_ms")
	if err != nil {
		return 0, err
	}
	kind, err := stringField(fields["type"])
	if err != nil || kind != "authorized" {
		return 0, ErrProtocol
	}
	token, err := stringField(fields["claim_token"])
	if err != nil || token != claim {
		return 0, ErrProtocol
	}
	lease, err := integerField(fields["lease_until_ms"])
	if err != nil || lease <= previousLease || lease > maxIdentityInteger {
		return 0, ErrProtocol
	}
	return lease, nil
}

func DecodeAttestation(payload []byte, shard string, epoch int64) error {
	fields, err := exactObject(payload, "version", "type", "shard_id", "routing_epoch")
	if err != nil {
		return err
	}
	version, err := stringField(fields["version"])
	if err != nil || version != Protocol {
		return ErrProtocol
	}
	kind, err := stringField(fields["type"])
	if err != nil || kind != "attest_route" {
		return ErrProtocol
	}
	requestedShard, err := stringField(fields["shard_id"])
	if err != nil || requestedShard != shard {
		return ErrProtocol
	}
	requestedEpoch, err := integerField(fields["routing_epoch"])
	if err != nil || requestedEpoch != epoch {
		return ErrProtocol
	}
	return nil
}
