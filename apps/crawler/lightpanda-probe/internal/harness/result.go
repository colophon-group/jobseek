package harness

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/encoding/protojson"
)

func SHA256(data []byte) string {
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

func InlineManifest(data []byte) *runtimev1.ChunkManifest {
	digest := SHA256(data)
	return &runtimev1.ChunkManifest{
		Chunks: []*runtimev1.DataChunk{{
			Sequence:  0,
			SizeBytes: uint64(len(data)),
			Sha256:    digest,
			Storage:   &runtimev1.DataChunk_InlineBody{InlineBody: data},
		}},
		TotalSizeBytes: uint64(len(data)),
		TotalSha256:    digest,
		Complete:       true,
	}
}

func EvaluationEnvelope(data []byte) *runtimev1.ExtensionEnvelope {
	return &runtimev1.ExtensionEnvelope{
		SchemaId:      "jobseek.lightpanda-probe.evaluation-json",
		SchemaVersion: 1,
		Encoding:      runtimev1.ExtensionEncoding_EXTENSION_ENCODING_CANONICAL_JSON,
		Payload:       data,
		PayloadSha256: SHA256(data),
	}
}

func MarshalResult(result *runtimev1.BrowserResult) (json.RawMessage, error) {
	data, err := (protojson.MarshalOptions{
		UseProtoNames:   true,
		EmitUnpopulated: false,
	}).Marshal(result)
	if err != nil {
		return nil, fmt.Errorf("marshal browser result: %w", err)
	}
	return json.RawMessage(data), nil
}

func MarshalEnvelope(envelope Envelope) ([]byte, error) {
	if envelope.Ledger == nil {
		envelope.Ledger = []LedgerEntry{}
	} else {
		envelope.Ledger = append([]LedgerEntry(nil), envelope.Ledger...)
	}
	if err := normalizeRequestLimitSnapshot(&envelope); err != nil {
		return nil, err
	}
	SortLedger(envelope.Ledger)
	data, err := json.MarshalIndent(envelope, "", "  ")
	if err != nil {
		return nil, fmt.Errorf("marshal probe envelope: %w", err)
	}
	return append(data, '\n'), nil
}

func normalizeRequestLimitSnapshot(envelope *Envelope) error {
	hasRequestLimit := false
	for _, entry := range envelope.Ledger {
		if entry.Reason == "request_limit" {
			hasRequestLimit = true
			break
		}
	}
	if !hasRequestLimit {
		return nil
	}

	var outcome struct {
		Error struct {
			Error struct {
				Code string `json:"code"`
			} `json:"error"`
		} `json:"error"`
	}
	if err := json.Unmarshal(envelope.Result, &outcome); err != nil {
		return fmt.Errorf("inspect request-limit result: %w", err)
	}
	if outcome.Error.Error.Code != runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT.String() {
		return nil
	}

	var responseBytes uint64
	for index := range envelope.Ledger {
		entry := &envelope.Ledger[index]
		if entry.Decision == "allowed" && entry.ResourceType != "Document" && entry.ResourceType != "Policy" {
			entry.Status = 0
			entry.ResponseBytes = 0
		}
		if entry.ResponseBytes > math.MaxUint64-responseBytes {
			return fmt.Errorf("recompute request-limit response bytes: overflow")
		}
		responseBytes += entry.ResponseBytes
	}
	envelope.ResponseBytes = responseBytes
	return nil
}
