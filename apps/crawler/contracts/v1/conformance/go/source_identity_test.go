package adjacentpolicy

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type sourceIdentityWireVector struct {
	Message              string `json:"message"`
	SourceIdentity       string `json:"source_identity"`
	AbsentWireHex        string `json:"absent_wire_hex"`
	PresentWireHex       string `json:"present_wire_hex"`
	FutureUnknownField   int    `json:"future_unknown_field"`
	FutureUnknownWireHex string `json:"future_unknown_wire_hex"`
}

type sourceIdentityFieldSchema struct {
	JSONName       string `json:"json_name"`
	Name           string `json:"name"`
	Number         int    `json:"number"`
	Proto3Optional bool   `json:"proto3_optional"`
	ValueType      string `json:"value_type"`
	WireType       int    `json:"wire_type"`
}

type sourceIdentityMessageSchema struct {
	Fields []sourceIdentityFieldSchema `json:"fields"`
}

type sourceIdentitySchemaVersion struct {
	Messages    map[string]sourceIdentityMessageSchema `json:"messages"`
	ShapeSHA256 string                                 `json:"shape_sha256"`
}

type sourceIdentityJSONVector struct {
	ID               string         `json:"id"`
	Job              map[string]any `json:"job"`
	CanonicalJSONHex string         `json:"canonical_json_hex"`
}

type sourceIdentityVectors struct {
	Format   int                                    `json:"format"`
	Schemas  map[string]sourceIdentitySchemaVersion `json:"schemas"`
	Protobuf []sourceIdentityWireVector             `json:"protobuf"`
	JSONJobs []sourceIdentityJSONVector             `json:"json_jobs"`
}

type sourceIdentityWireField struct {
	Number   int
	WireType int
	Payload  []byte
	Raw      []byte
}

type sourceIdentityKnownValue struct {
	Field sourceIdentityFieldSchema
	Value any
}

type sourceIdentityDecodedMessage struct {
	Known   []sourceIdentityKnownValue
	Unknown []sourceIdentityWireField
}

func sourceIdentityFixtureRoot(t *testing.T) string {
	t.Helper()
	return filepath.Join(contractRoot(t), "fixtures", "source_identity")
}

func readSourceIdentityVectors(t *testing.T) sourceIdentityVectors {
	t.Helper()
	var vectors sourceIdentityVectors
	content, err := os.ReadFile(filepath.Join(sourceIdentityFixtureRoot(t), "vectors.json"))
	if err != nil {
		t.Fatal(err)
	}
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&vectors); err != nil {
		t.Fatal(err)
	}
	return vectors
}

func readSourceIdentityVarint(wire []byte, offset *int) (uint64, error) {
	var value uint64
	for shift := uint(0); *offset < len(wire) && shift < 70; shift += 7 {
		b := wire[*offset]
		*offset = *offset + 1
		value |= uint64(b&0x7f) << shift
		if b < 0x80 {
			return value, nil
		}
	}
	return 0, fmt.Errorf("malformed fixture varint")
}

func parseSourceIdentityWire(wire []byte) ([]sourceIdentityWireField, error) {
	fields := make([]sourceIdentityWireField, 0)
	for offset := 0; offset < len(wire); {
		start := offset
		tag, err := readSourceIdentityVarint(wire, &offset)
		if err != nil {
			return nil, err
		}
		number, wireType := int(tag>>3), int(tag&7)
		if number == 0 {
			return nil, fmt.Errorf("protobuf field zero")
		}
		var payload []byte
		switch wireType {
		case 0:
			if _, err := readSourceIdentityVarint(wire, &offset); err != nil {
				return nil, err
			}
		case 1:
			if offset+8 > len(wire) {
				return nil, fmt.Errorf("truncated fixed64")
			}
			payload = wire[offset : offset+8]
			offset += 8
		case 2:
			size, err := readSourceIdentityVarint(wire, &offset)
			if err != nil || size > uint64(len(wire)-offset) {
				return nil, fmt.Errorf("truncated bytes field")
			}
			payload = wire[offset : offset+int(size)]
			offset += int(size)
		case 5:
			if offset+4 > len(wire) {
				return nil, fmt.Errorf("truncated fixed32")
			}
			payload = wire[offset : offset+4]
			offset += 4
		default:
			return nil, fmt.Errorf("unsupported fixture wire type: %d", wireType)
		}
		fields = append(fields, sourceIdentityWireField{
			Number: number, WireType: wireType, Payload: payload, Raw: wire[start:offset],
		})
	}
	return fields, nil
}

func decodeSourceIdentityMessage(
	wire []byte,
	schema sourceIdentityMessageSchema,
) (sourceIdentityDecodedMessage, error) {
	fields, err := parseSourceIdentityWire(wire)
	if err != nil {
		return sourceIdentityDecodedMessage{}, err
	}
	byNumber := make(map[int]sourceIdentityFieldSchema, len(schema.Fields))
	for _, field := range schema.Fields {
		byNumber[field.Number] = field
	}
	decoded := sourceIdentityDecodedMessage{}
	for _, field := range fields {
		specification, known := byNumber[field.Number]
		if !known {
			decoded.Unknown = append(decoded.Unknown, field)
			continue
		}
		if field.WireType != specification.WireType {
			return sourceIdentityDecodedMessage{}, fmt.Errorf(
				"wire type mismatch for %s: %d != %d",
				specification.Name,
				field.WireType,
				specification.WireType,
			)
		}
		var value any
		switch specification.ValueType {
		case "string":
			value = string(field.Payload)
		case "message":
			value = bytes.Clone(field.Payload)
		default:
			return sourceIdentityDecodedMessage{}, fmt.Errorf(
				"unsupported descriptor value type: %s",
				specification.ValueType,
			)
		}
		decoded.Known = append(decoded.Known, sourceIdentityKnownValue{
			Field: specification,
			Value: value,
		})
	}
	return decoded, nil
}

func appendSourceIdentityVarint(output []byte, value uint64) []byte {
	for value >= 0x80 {
		output = append(output, byte(value)|0x80)
		value >>= 7
	}
	return append(output, byte(value))
}

func encodeSourceIdentityMessage(message sourceIdentityDecodedMessage) ([]byte, error) {
	encoded := make([]byte, 0)
	for _, known := range message.Known {
		var payload []byte
		switch known.Field.ValueType {
		case "string":
			value, ok := known.Value.(string)
			if !ok {
				return nil, fmt.Errorf("decoded %s is not a string", known.Field.Name)
			}
			payload = []byte(value)
		case "message":
			value, ok := known.Value.([]byte)
			if !ok {
				return nil, fmt.Errorf("decoded %s is not a message", known.Field.Name)
			}
			payload = value
		default:
			return nil, fmt.Errorf("unsupported descriptor value type: %s", known.Field.ValueType)
		}
		encoded = appendSourceIdentityVarint(
			encoded,
			uint64((known.Field.Number<<3)|known.Field.WireType),
		)
		encoded = appendSourceIdentityVarint(encoded, uint64(len(payload)))
		encoded = append(encoded, payload...)
	}
	for _, unknown := range message.Unknown {
		encoded = append(encoded, unknown.Raw...)
	}
	return encoded, nil
}

func sourceIdentityStringValue(message sourceIdentityDecodedMessage) (string, bool, error) {
	var result string
	present := false
	for _, known := range message.Known {
		if known.Field.Name != "source_identity" {
			continue
		}
		if present {
			return "", false, fmt.Errorf("duplicate source_identity fixture field")
		}
		value, ok := known.Value.(string)
		if !ok {
			return "", false, fmt.Errorf("source_identity is not decoded as string")
		}
		result, present = value, true
	}
	return result, present, nil
}

func decodeSourceIdentityHex(t *testing.T, value string) []byte {
	t.Helper()
	wire, err := hex.DecodeString(value)
	if err != nil {
		t.Fatal(err)
	}
	return wire
}

func TestSourceIdentitySharedWireVectorsPreserveCompatibility(t *testing.T) {
	vectors := readSourceIdentityVectors(t)
	if vectors.Format != 1 || len(vectors.Protobuf) != 2 {
		t.Fatalf("unexpected source-identity corpus shape: format=%d vectors=%d", vectors.Format, len(vectors.Protobuf))
	}
	messages := map[string]bool{}
	for _, vector := range vectors.Protobuf {
		messages[vector.Message] = true
		frozenSchema := vectors.Schemas["frozen"].Messages[vector.Message]
		currentSchema := vectors.Schemas["current"].Messages[vector.Message]
		for _, field := range frozenSchema.Fields {
			if field.Name == "source_identity" {
				t.Fatalf("%s frozen schema unexpectedly has source_identity", vector.Message)
			}
		}
		var identityField *sourceIdentityFieldSchema
		for index := range currentSchema.Fields {
			if currentSchema.Fields[index].Name == "source_identity" {
				identityField = &currentSchema.Fields[index]
			}
		}
		if identityField == nil || identityField.Number != 3 || identityField.WireType != 2 ||
			identityField.ValueType != "string" || !identityField.Proto3Optional ||
			identityField.JSONName != "sourceIdentity" {
			t.Fatalf("%s current descriptor has wrong source_identity shape: %#v", vector.Message, identityField)
		}
		absent := decodeSourceIdentityHex(t, vector.AbsentWireHex)
		present := decodeSourceIdentityHex(t, vector.PresentWireHex)
		future := decodeSourceIdentityHex(t, vector.FutureUnknownWireHex)

		oldAbsent, err := decodeSourceIdentityMessage(absent, frozenSchema)
		if err != nil {
			t.Fatal(err)
		}
		currentAbsent, err := decodeSourceIdentityMessage(absent, currentSchema)
		if err != nil {
			t.Fatal(err)
		}
		for _, decoded := range []sourceIdentityDecodedMessage{oldAbsent, currentAbsent} {
			_, identityPresent, valueErr := sourceIdentityStringValue(decoded)
			if valueErr != nil || identityPresent {
				t.Fatalf("%s absent identity did not remain absent: %v", vector.Message, valueErr)
			}
		}
		forwarded, err := encodeSourceIdentityMessage(oldAbsent)
		if err != nil || !bytes.Equal(forwarded, absent) {
			t.Fatalf("%s legacy absent round trip changed bytes: %v", vector.Message, err)
		}
		forwarded, err = encodeSourceIdentityMessage(currentAbsent)
		if err != nil || !bytes.Equal(forwarded, absent) {
			t.Fatalf("%s current absent round trip changed bytes: %v", vector.Message, err)
		}

		currentPresent, err := decodeSourceIdentityMessage(present, currentSchema)
		if err != nil {
			t.Fatal(err)
		}
		value, identityPresent, err := sourceIdentityStringValue(currentPresent)
		if err != nil || !identityPresent || value != vector.SourceIdentity {
			t.Fatalf("%s present identity mismatch: %q %t %v", vector.Message, value, identityPresent, err)
		}
		forwarded, err = encodeSourceIdentityMessage(currentPresent)
		if err != nil || !bytes.Equal(forwarded, present) {
			t.Fatalf("%s current present round trip changed bytes: %v", vector.Message, err)
		}

		oldPresent, err := decodeSourceIdentityMessage(present, frozenSchema)
		if err != nil {
			t.Fatal(err)
		}
		_, identityPresent, err = sourceIdentityStringValue(oldPresent)
		if err != nil || identityPresent || len(oldPresent.Unknown) != 1 || oldPresent.Unknown[0].Number != 3 {
			t.Fatalf("%s legacy reader did not retain tag 3 as unknown: %#v %v", vector.Message, oldPresent.Unknown, err)
		}
		forwarded, err = encodeSourceIdentityMessage(oldPresent)
		if err != nil || !bytes.Equal(forwarded, present) {
			t.Fatalf("%s legacy reader dropped tag 3: %v", vector.Message, err)
		}

		currentFuture, err := decodeSourceIdentityMessage(future, currentSchema)
		if err != nil {
			t.Fatal(err)
		}
		if len(currentFuture.Unknown) != 1 || currentFuture.Unknown[0].Number != vector.FutureUnknownField {
			t.Fatalf("%s future unknown field was not retained: %#v", vector.Message, currentFuture.Unknown)
		}
		value, identityPresent, err = sourceIdentityStringValue(currentFuture)
		if err != nil || !identityPresent || value != vector.SourceIdentity {
			t.Fatalf("%s future payload lost source identity: %q %t %v", vector.Message, value, identityPresent, err)
		}
		forwarded, err = encodeSourceIdentityMessage(currentFuture)
		if err != nil || !bytes.Equal(forwarded, future) {
			t.Fatalf("%s current reader dropped future field: %v", vector.Message, err)
		}
	}
	if !messages["DiscoveredJob"] || !messages["JobEffect"] {
		t.Fatalf("wire vectors do not cover both amended messages: %v", messages)
	}
}

func TestSourceIdentitySharedJSONVectorsPreserveOptionalProperty(t *testing.T) {
	vectors := readSourceIdentityVectors(t)
	if len(vectors.JSONJobs) != 3 {
		t.Fatalf("unexpected JSON vector count: %d", len(vectors.JSONJobs))
	}
	seen := map[string]bool{}
	for _, vector := range vectors.JSONJobs {
		seen[vector.ID] = true
		canonical, err := json.Marshal(vector.Job)
		if err != nil {
			t.Fatal(err)
		}
		if hex.EncodeToString(canonical) != vector.CanonicalJSONHex {
			t.Fatalf("canonical JSON changed for %s", vector.ID)
		}
		value, present := vector.Job["source_identity"]
		switch vector.ID {
		case "absent":
			if present {
				t.Fatal("legacy JSON unexpectedly gained source_identity")
			}
		case "explicit-null":
			if !present || value != nil {
				t.Fatalf("explicit null lost presence: %#v", value)
			}
		case "present":
			if value != "smartrecruiters:example:42" {
				t.Fatalf("present source_identity changed: %#v", value)
			}
		}
	}
	for _, identifier := range []string{"absent", "explicit-null", "present"} {
		if !seen[identifier] {
			t.Fatalf("missing JSON vector: %s", identifier)
		}
	}
}

func TestSourceIdentityVectorsMatchFrozenDigest(t *testing.T) {
	root := sourceIdentityFixtureRoot(t)
	raw, err := os.ReadFile(filepath.Join(root, "vectors.json"))
	if err != nil {
		t.Fatal(err)
	}
	digest, err := os.ReadFile(filepath.Join(root, "vectors.sha256"))
	if err != nil {
		t.Fatal(err)
	}
	expected := strings.Fields(string(digest))
	if len(expected) != 2 || expected[1] != "vectors.json" {
		t.Fatalf("malformed source-identity digest: %q", digest)
	}
	actual := fmt.Sprintf("%x", sha256.Sum256(raw))
	if actual != expected[0] {
		t.Fatalf("source-identity vector digest mismatch: %s != %s", actual, expected[0])
	}
}
