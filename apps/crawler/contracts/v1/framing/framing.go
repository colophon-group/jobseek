// Package framing implements checked unsigned-varint framing for raw records.
//
// The package intentionally has no protobuf or runtime-contract dependency.
package framing

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
)

// ErrorCode is a stable cross-language framing failure classification.
type ErrorCode string

const (
	CodeNonminimalPrefix ErrorCode = "nonminimal_prefix"
	CodePrefixOverflow   ErrorCode = "prefix_overflow"
	CodeFrameLimit       ErrorCode = "frame_limit"
	CodeTruncatedPrefix  ErrorCode = "truncated_prefix"
	CodeTruncatedPayload ErrorCode = "truncated_payload"
	CodeTrailingBytes    ErrorCode = "trailing_bytes"
	CodeAmbiguousEOF     ErrorCode = "ambiguous_eof"
	CodeReaderContract   ErrorCode = "reader_contract"
)

var (
	ErrNonminimalPrefix = errors.New("unsigned-varint prefix is not canonical")
	ErrPrefixOverflow   = errors.New("unsigned-varint prefix exceeds uint64")
	ErrFrameLimit       = errors.New("prefix plus payload exceeds maximum")
	ErrTruncatedPrefix  = errors.New("record ended inside the unsigned-varint prefix")
	ErrTruncatedPayload = errors.New("record ended before its declared payload length")
	ErrTrailingBytes    = errors.New("bytes remain after the exact record")
	ErrAmbiguousEOF     = errors.New("EOF occurred after a record began")
	ErrReaderContract   = errors.New("reader made no progress or violated io.Reader")
)

// FramingError carries a stable code and unwraps to a category sentinel.
type FramingError struct {
	Code   ErrorCode
	Detail string
}

func (err *FramingError) Error() string {
	return fmt.Sprintf("%s: %s", err.Code, err.Detail)
}

func (err *FramingError) Unwrap() error {
	switch err.Code {
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

func fail(code ErrorCode, detail string) error {
	return &FramingError{Code: code, Detail: detail}
}

// UvarintSize returns the canonical base-128 little-endian uint64 prefix size.
func UvarintSize(value uint64) uint64 {
	size := uint64(1)
	for value >= 0x80 {
		value >>= 7
		size++
	}
	return size
}

func checkCap(length uint64, prefixLength uint64, maximum uint64) error {
	// The subtraction form is deliberate: prefixLength+length may overflow.
	if maximum < prefixLength || length > maximum-prefixLength {
		return fail(CodeFrameLimit, "prefix plus payload exceeds maximum")
	}
	return nil
}

func decodePrefix(data []byte) (uint64, int, error) {
	if len(data) == 0 {
		return 0, 0, fail(CodeTruncatedPrefix, "record ended before the unsigned-varint prefix")
	}

	var value uint64
	limit := len(data)
	if limit > binary.MaxVarintLen64 {
		limit = binary.MaxVarintLen64
	}
	for index := 0; index < limit; index++ {
		unit := data[index]
		if index == binary.MaxVarintLen64-1 && (unit > 1 || unit&0x80 != 0) {
			return 0, 0, fail(CodePrefixOverflow, "unsigned-varint prefix exceeds uint64")
		}
		value |= uint64(unit&0x7f) << (7 * index)
		if unit < 0x80 {
			prefixLength := index + 1
			if uint64(prefixLength) != UvarintSize(value) {
				return 0, 0, fail(CodeNonminimalPrefix, "unsigned-varint prefix is not canonical")
			}
			return value, prefixLength, nil
		}
	}

	if len(data) < binary.MaxVarintLen64 {
		return 0, 0, fail(CodeTruncatedPrefix, "record ended inside the unsigned-varint prefix")
	}
	return 0, 0, fail(CodePrefixOverflow, "unsigned-varint prefix has more than ten bytes")
}

// EncodeRecord encodes one raw record after checking the prefix-inclusive cap.
func EncodeRecord(payload []byte, maximum uint64) ([]byte, error) {
	length := uint64(len(payload))
	prefixLength := UvarintSize(length)
	if err := checkCap(length, prefixLength, maximum); err != nil {
		return nil, err
	}
	maxInt := int(^uint(0) >> 1)
	if len(payload) > maxInt-int(prefixLength) {
		return nil, fail(CodeFrameLimit, "encoded record cannot fit in an in-memory byte slice")
	}
	prefix := make([]byte, binary.MaxVarintLen64)
	written := binary.PutUvarint(prefix, length)
	record := make([]byte, written+len(payload))
	copy(record, prefix[:written])
	copy(record[written:], payload)
	return record, nil
}

// DecodeNext decodes the first record and returns the untouched remainder.
func DecodeNext(data []byte, maximum uint64) ([]byte, []byte, error) {
	length, prefixLength, err := decodePrefix(data)
	if err != nil {
		return nil, nil, err
	}
	if err := checkCap(length, uint64(prefixLength), maximum); err != nil {
		return nil, nil, err
	}
	available := uint64(len(data) - prefixLength)
	if available < length {
		return nil, nil, fail(CodeTruncatedPayload, "record ended before its declared payload length")
	}
	// length is bounded by an existing slice length before the int conversion.
	end := prefixLength + int(length)
	return data[prefixLength:end], data[end:], nil
}

// DecodeRecord decodes exactly one record and rejects trailing bytes.
func DecodeRecord(data []byte, maximum uint64) ([]byte, error) {
	payload, remainder, err := DecodeNext(data, maximum)
	if err != nil {
		return nil, err
	}
	if len(remainder) != 0 {
		return nil, fail(CodeTrailingBytes, "bytes remain after the exact record")
	}
	return payload, nil
}

func readChunk(reader io.Reader, buffer []byte) (int, error) {
	count, err := reader.Read(buffer)
	if count < 0 || count > len(buffer) {
		return 0, fail(CodeReaderContract, "reader returned an invalid byte count")
	}
	if count == 0 && err == nil {
		return 0, fail(CodeReaderContract, "reader returned no bytes and no error")
	}
	return count, err
}

// ReadRecord reads one record without prefetching the next.
//
// io.EOF before the prefix is clean. EOF after any record byte is the typed
// ambiguous_eof failure. Memory is bounded by one accepted record, and the
// caller supplies backpressure by choosing when to request another record.
func ReadRecord(reader io.Reader, maximum uint64) ([]byte, error) {
	prefix := make([]byte, 0, binary.MaxVarintLen64)
	var one [1]byte
	for len(prefix) < binary.MaxVarintLen64 {
		count, readErr := readChunk(reader, one[:])
		if count == 0 {
			if errors.Is(readErr, io.EOF) {
				if len(prefix) == 0 {
					return nil, io.EOF
				}
				return nil, fail(CodeAmbiguousEOF, "EOF occurred inside the record prefix")
			}
			return nil, readErr
		}
		prefix = append(prefix, one[0])
		if len(prefix) == binary.MaxVarintLen64 && (one[0] > 1 || one[0]&0x80 != 0) {
			return nil, fail(CodePrefixOverflow, "unsigned-varint prefix exceeds uint64")
		}
		if one[0] < 0x80 {
			length, prefixLength, err := decodePrefix(prefix)
			if err != nil {
				return nil, err
			}
			if err := checkCap(length, uint64(prefixLength), maximum); err != nil {
				return nil, err
			}
			return readPayload(reader, length, readErr)
		}
		if readErr != nil {
			if errors.Is(readErr, io.EOF) {
				return nil, fail(CodeAmbiguousEOF, "EOF occurred inside the record prefix")
			}
			return nil, readErr
		}
	}
	return nil, fail(CodePrefixOverflow, "unsigned-varint prefix has more than ten bytes")
}

func readPayload(reader io.Reader, length uint64, prefixReadErr error) ([]byte, error) {
	if length == 0 {
		return []byte{}, nil
	}
	if prefixReadErr != nil {
		if errors.Is(prefixReadErr, io.EOF) {
			return nil, fail(CodeAmbiguousEOF, "EOF occurred before the record payload")
		}
		return nil, prefixReadErr
	}

	const chunkSize = 64 * 1024
	capacity := length
	if capacity > chunkSize {
		capacity = chunkSize
	}
	payload := make([]byte, 0, int(capacity))
	remaining := length
	buffer := make([]byte, chunkSize)
	for remaining != 0 {
		want := remaining
		if want > chunkSize {
			want = chunkSize
		}
		count, readErr := readChunk(reader, buffer[:int(want)])
		if count != 0 {
			payload = append(payload, buffer[:count]...)
			remaining -= uint64(count)
		}
		if remaining == 0 {
			return payload, nil
		}
		if readErr != nil {
			if errors.Is(readErr, io.EOF) {
				return nil, fail(CodeAmbiguousEOF, "EOF occurred inside the record payload")
			}
			return nil, readErr
		}
	}
	return payload, nil
}
