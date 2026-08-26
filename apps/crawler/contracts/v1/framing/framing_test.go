package framing

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

type roundtripVector struct {
	Name             string `json:"name"`
	Maximum          uint64 `json:"maximum"`
	PayloadHex       string `json:"payload_hex"`
	PayloadRepeatHex string `json:"payload_repeat_hex"`
	PayloadLength    int    `json:"payload_length"`
	WirePrefixHex    string `json:"wire_prefix_hex"`
	WireSHA256       string `json:"wire_sha256"`
}

type errorVector struct {
	Name             string `json:"name"`
	Maximum          uint64 `json:"maximum"`
	PayloadHex       string `json:"payload_hex"`
	PayloadRepeatHex string `json:"payload_repeat_hex"`
	PayloadLength    int    `json:"payload_length"`
	WireHex          string `json:"wire_hex"`
	ExpectedError    string `json:"expected_error"`
}

type streamVector struct {
	Name           string   `json:"name"`
	Maximum        uint64   `json:"maximum"`
	WireHex        string   `json:"wire_hex"`
	FragmentSize   int      `json:"fragment_size"`
	PayloadsHex    []string `json:"payloads_hex"`
	PayloadsSHA256 []string `json:"payloads_sha256"`
	ExpectedError  string   `json:"expected_error"`
}

type corpusFile struct {
	Roundtrip       []roundtripVector `json:"roundtrip"`
	EncodeErrors    []errorVector     `json:"encode_errors"`
	Decode          []errorVector     `json:"decode"`
	Streams         []streamVector    `json:"streams"`
	PropertyLengths []int             `json:"property_lengths"`
}

func loadCorpus(t *testing.T) corpusFile {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "fixtures", "framing", "vectors.json"))
	if err != nil {
		t.Fatal(err)
	}
	var corpus corpusFile
	if err := json.Unmarshal(raw, &corpus); err != nil {
		t.Fatal(err)
	}
	return corpus
}

func vectorPayload(t *testing.T, vector interface {
	payloadParts() (string, string, int)
}) []byte {
	t.Helper()
	payloadHex, repeatHex, length := vector.payloadParts()
	if payloadHex != "" {
		payload, err := hex.DecodeString(payloadHex)
		if err != nil {
			t.Fatal(err)
		}
		return payload
	}
	unit, err := hex.DecodeString(repeatHex)
	if err != nil {
		t.Fatal(err)
	}
	return bytes.Repeat(unit, length)
}

func (vector roundtripVector) payloadParts() (string, string, int) {
	return vector.PayloadHex, vector.PayloadRepeatHex, vector.PayloadLength
}

func (vector errorVector) payloadParts() (string, string, int) {
	return vector.PayloadHex, vector.PayloadRepeatHex, vector.PayloadLength
}

func sentinel(code string) error {
	switch ErrorCode(code) {
	case CodeNonminimalPrefix:
		return ErrNonminimalPrefix
	case CodePrefixOverflow:
		return ErrPrefixOverflow
	case CodeFrameLimit:
		return ErrFrameLimit
	case CodeTruncatedPrefix:
		return ErrTruncatedPrefix
	case CodeTruncatedPayload:
		return ErrTruncatedPayload
	case CodeTrailingBytes:
		return ErrTrailingBytes
	case CodeAmbiguousEOF:
		return ErrAmbiguousEOF
	case CodeReaderContract:
		return ErrReaderContract
	default:
		return nil
	}
}

func requireCode(t *testing.T, err error, expected string) {
	t.Helper()
	if err == nil {
		t.Fatalf("got nil, want %s", expected)
	}
	var typed *FramingError
	if !errors.As(err, &typed) {
		t.Fatalf("%T does not unwrap as FramingError: %v", err, err)
	}
	if string(typed.Code) != expected {
		t.Fatalf("got code %s, want %s", typed.Code, expected)
	}
	if category := sentinel(expected); category == nil || !errors.Is(err, category) {
		t.Fatalf("%v does not unwrap to %v", err, category)
	}
}

func TestSharedRoundtripVectors(t *testing.T) {
	for _, vector := range loadCorpus(t).Roundtrip {
		vector := vector
		t.Run(vector.Name, func(t *testing.T) {
			payload := vectorPayload(t, vector)
			wire, err := EncodeRecord(payload, vector.Maximum)
			if err != nil {
				t.Fatal(err)
			}
			prefix, err := hex.DecodeString(vector.WirePrefixHex)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.HasPrefix(wire, prefix) {
				t.Fatalf("wire %x does not start with %x", wire, prefix)
			}
			digest := fmt.Sprintf("%x", sha256.Sum256(wire))
			if digest != vector.WireSHA256 {
				t.Fatalf("got digest %s, want %s", digest, vector.WireSHA256)
			}
			decoded, err := DecodeRecord(wire, vector.Maximum)
			if err != nil || !bytes.Equal(decoded, payload) {
				t.Fatalf("decode mismatch: %v", err)
			}
		})
	}
}

func TestSharedEncodeErrorVectors(t *testing.T) {
	for _, vector := range loadCorpus(t).EncodeErrors {
		vector := vector
		t.Run(vector.Name, func(t *testing.T) {
			_, err := EncodeRecord(vectorPayload(t, vector), vector.Maximum)
			requireCode(t, err, vector.ExpectedError)
		})
	}
}

func TestSharedExactDecodeErrorVectors(t *testing.T) {
	for _, vector := range loadCorpus(t).Decode {
		vector := vector
		t.Run(vector.Name, func(t *testing.T) {
			wire, err := hex.DecodeString(vector.WireHex)
			if err != nil {
				t.Fatal(err)
			}
			_, decodeErr := DecodeRecord(wire, vector.Maximum)
			requireCode(t, decodeErr, vector.ExpectedError)
		})
	}
}

type fragmentReader struct {
	data         []byte
	fragmentSize int
	offset       int
}

func (reader *fragmentReader) Read(buffer []byte) (int, error) {
	if reader.offset == len(reader.data) {
		return 0, io.EOF
	}
	count := len(buffer)
	if count > reader.fragmentSize {
		count = reader.fragmentSize
	}
	remaining := len(reader.data) - reader.offset
	if count > remaining {
		count = remaining
	}
	copy(buffer, reader.data[reader.offset:reader.offset+count])
	reader.offset += count
	return count, nil
}

func TestSharedStreamVectors(t *testing.T) {
	for _, vector := range loadCorpus(t).Streams {
		vector := vector
		t.Run(vector.Name, func(t *testing.T) {
			wire, err := hex.DecodeString(vector.WireHex)
			if err != nil {
				t.Fatal(err)
			}
			reader := &fragmentReader{data: wire, fragmentSize: vector.FragmentSize}
			var payloads [][]byte
			for {
				payload, readErr := ReadRecord(reader, vector.Maximum)
				if errors.Is(readErr, io.EOF) {
					break
				}
				if readErr != nil {
					if vector.ExpectedError == "" {
						t.Fatal(readErr)
					}
					requireCode(t, readErr, vector.ExpectedError)
					return
				}
				payloads = append(payloads, payload)
			}
			if vector.ExpectedError != "" {
				t.Fatalf("got clean EOF, want %s", vector.ExpectedError)
			}
			if len(vector.PayloadsHex) != 0 || len(payloads) == 0 {
				actual := make([]string, len(payloads))
				for index, payload := range payloads {
					actual[index] = hex.EncodeToString(payload)
				}
				if strings.Join(actual, ",") != strings.Join(vector.PayloadsHex, ",") {
					t.Fatalf("got payloads %v, want %v", actual, vector.PayloadsHex)
				}
				return
			}
			actual := make([]string, len(payloads))
			for index, payload := range payloads {
				actual[index] = fmt.Sprintf("%x", sha256.Sum256(payload))
			}
			if strings.Join(actual, ",") != strings.Join(vector.PayloadsSHA256, ",") {
				t.Fatalf("got payload digests %v, want %v", actual, vector.PayloadsSHA256)
			}
		})
	}
}

func TestExactBufferAndStreamEOFHaveDistinctContracts(t *testing.T) {
	_, err := DecodeRecord(nil, 1)
	requireCode(t, err, string(CodeTruncatedPrefix))
	if _, err := ReadRecord(bytes.NewReader(nil), 1); !errors.Is(err, io.EOF) {
		t.Fatalf("got %v, want clean io.EOF", err)
	}
	payload, err := DecodeRecord([]byte{0}, 1)
	if err != nil || len(payload) != 0 {
		t.Fatalf("empty record failed: %v", err)
	}
	_, err = DecodeRecord([]byte{0, 0}, 1)
	requireCode(t, err, string(CodeTrailingBytes))
}

func TestDecodeNextLeavesConcatenatedRecordsUnconsumed(t *testing.T) {
	remainder := []byte{1, 'a', 1, 'b', 0}
	var payloads [][]byte
	for len(remainder) != 0 {
		payload, next, err := DecodeNext(remainder, 2)
		if err != nil {
			t.Fatal(err)
		}
		payloads = append(payloads, append([]byte(nil), payload...))
		remainder = next
	}
	if fmt.Sprintf("%q", payloads) != fmt.Sprintf("%q", [][]byte{{'a'}, {'b'}, {}}) {
		t.Fatalf("unexpected payloads: %q", payloads)
	}
}

func TestPrefixInclusiveCapProperty(t *testing.T) {
	lengths := append([]int(nil), loadCorpus(t).PropertyLengths...)
	random := rand.New(rand.NewSource(7937))
	for range 200 {
		lengths = append(lengths, random.Intn(100_000))
	}
	for _, length := range lengths {
		payload := bytes.Repeat([]byte{byte(length % 251)}, length)
		exact := uint64(length) + UvarintSize(uint64(length))
		wire, err := EncodeRecord(payload, exact)
		if err != nil {
			t.Fatalf("length %d exact cap: %v", length, err)
		}
		decoded, err := DecodeRecord(wire, exact)
		if err != nil || !bytes.Equal(decoded, payload) {
			t.Fatalf("length %d decode: %v", length, err)
		}
		_, err = EncodeRecord(payload, exact-1)
		requireCode(t, err, string(CodeFrameLimit))
	}
}

type zeroProgressReader struct{}

func (zeroProgressReader) Read([]byte) (int, error) { return 0, nil }

func TestReaderZeroProgressIsTypedFailure(t *testing.T) {
	_, err := ReadRecord(zeroProgressReader{}, 1)
	requireCode(t, err, string(CodeReaderContract))
}

func TestSharedCorpusDigest(t *testing.T) {
	path := filepath.Join("..", "fixtures", "framing", "vectors.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	digestFile, err := os.ReadFile(filepath.Join("..", "fixtures", "framing", "vectors.sha256"))
	if err != nil {
		t.Fatal(err)
	}
	expected := strings.Fields(string(digestFile))[0]
	actual := fmt.Sprintf("%x", sha256.Sum256(raw))
	if actual != expected {
		t.Fatalf("got digest %s, want %s", actual, expected)
	}
}

func FuzzDecodeRecordNeverPanics(f *testing.F) {
	seeds := []string{
		"",
		"00",
		"0000",
		"80",
		"8000",
		"ffffffffffffffffff01",
		"f7ffffffffffffffff01",
		"f5ffffffffffffffff01",
		"ffffffffffffffffff02",
		"8080808080808080808000",
	}
	for _, seed := range seeds {
		raw, err := hex.DecodeString(seed)
		if err != nil {
			f.Fatal(err)
		}
		f.Add(raw, strconv.FormatUint(^uint64(0), 10))
	}
	f.Fuzz(func(t *testing.T, data []byte, maximumText string) {
		maximum, err := strconv.ParseUint(maximumText, 10, 64)
		if err != nil {
			maximum = uint64(len(data))
		}
		_, _ = DecodeRecord(data, maximum)
	})
}
