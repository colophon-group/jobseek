package framing

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

func TestSharedFramingVectors(t *testing.T) {
	var cases []struct {
		Name          string `json:"name"`
		Hex           string `json:"hex"`
		MaxFrameBytes uint64 `json:"max_frame_bytes"`
		Error         string `json:"error"`
		Valid         bool   `json:"valid"`
	}
	raw, err := os.ReadFile(filepath.Join("..", "fixtures", "framing", "cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	for _, value := range cases {
		value := value
		t.Run(value.Name, func(t *testing.T) {
			wire, err := hex.DecodeString(value.Hex)
			if err != nil {
				t.Fatal(err)
			}
			remaining, err := Decode(wire, &runtimev1.ClientMessage{}, value.MaxFrameBytes)
			if value.Valid && (err != nil || len(remaining) != 0) {
				t.Fatalf("valid record rejected: remaining=%x err=%v", remaining, err)
			}
			if !value.Valid && err == nil {
				t.Fatal("invalid record accepted")
			}
			if !value.Valid {
				expected := map[string]error{
					"ambiguous_eof": ErrAmbiguousEOF,
					"malformed":     ErrMalformed,
					"oversize":      ErrOversize,
				}[value.Error]
				if !errors.Is(err, expected) {
					t.Fatalf("got %v, want classification %v", err, expected)
				}
			}
		})
	}
}

func TestRoundTripAndOversizeEncode(t *testing.T) {
	message := &runtimev1.ClientMessage{Payload: &runtimev1.ClientMessage_Hello{Hello: &runtimev1.ClientHello{}}}
	wire, err := Encode(message, 3)
	if err != nil {
		t.Fatal(err)
	}
	if remaining, err := Decode(wire, &runtimev1.ClientMessage{}, 3); err != nil || len(remaining) != 0 {
		t.Fatalf("round trip failed: remaining=%x err=%v", remaining, err)
	}
	if _, err := Encode(message, 0); err == nil {
		t.Fatal("zero frame ceiling accepted")
	}
}
