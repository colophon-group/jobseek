package harness

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"

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
	}
	SortLedger(envelope.Ledger)
	data, err := json.MarshalIndent(envelope, "", "  ")
	if err != nil {
		return nil, fmt.Errorf("marshal probe envelope: %w", err)
	}
	return append(data, '\n'), nil
}
