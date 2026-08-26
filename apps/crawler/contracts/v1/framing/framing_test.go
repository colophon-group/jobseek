package framing

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestSharedFramingVectors(t *testing.T) {
	var file struct {
		Vectors []struct {
			Name          string  `json:"name"`
			WireHex       string  `json:"wire_hex"`
			WirePrefixHex string  `json:"wire_prefix_hex"`
			PayloadHex    *string `json:"payload_hex"`
			PayloadByte   string  `json:"payload_byte"`
			PayloadLength int     `json:"payload_length"`
			Maximum       uint64  `json:"maximum"`
			ExpectedError string  `json:"expected_error"`
		} `json:"vectors"`
	}
	raw, err := os.ReadFile(filepath.Join("..", "fixtures", "wire", "framing-vectors.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &file); err != nil {
		t.Fatal(err)
	}
	for _, vector := range file.Vectors {
		vector := vector
		t.Run(vector.Name, func(t *testing.T) {
			if vector.PayloadHex != nil || vector.PayloadByte != "" {
				var payload []byte
				if vector.PayloadHex != nil {
					payload, err = hex.DecodeString(*vector.PayloadHex)
				} else {
					unit, decodeErr := hex.DecodeString(vector.PayloadByte)
					if decodeErr != nil {
						t.Fatal(decodeErr)
					}
					payload = bytes.Repeat(unit, vector.PayloadLength)
				}
				wire, encodeErr := EncodeRecord(payload, vector.Maximum)
				if encodeErr != nil {
					t.Fatal(encodeErr)
				}
				prefixHex := vector.WirePrefixHex
				if prefixHex == "" {
					prefixHex = vector.WireHex
				}
				prefix, _ := hex.DecodeString(prefixHex)
				if !bytes.HasPrefix(wire, prefix) {
					t.Fatalf("wire %x does not begin with %x", wire, prefix)
				}
				decoded, decodeErr := DecodeRecord(wire, vector.Maximum)
				if decodeErr != nil || !bytes.Equal(decoded, payload) {
					t.Fatalf("decode mismatch: %v", decodeErr)
				}
				return
			}
			wire, _ := hex.DecodeString(vector.WireHex)
			_, decodeErr := DecodeRecord(wire, vector.Maximum)
			expected := ErrFraming
			if vector.ExpectedError == "frame_limit" {
				expected = ErrFrameLimit
			}
			if !errors.Is(decodeErr, expected) {
				t.Fatalf("got %v, want classification %v", decodeErr, expected)
			}
		})
	}
}
