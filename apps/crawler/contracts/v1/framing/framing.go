// Package framing implements crawler.runtime/v1 unsigned-varint protobuf records.
package framing

import (
	"encoding/binary"
	"errors"
	"fmt"

	"google.golang.org/protobuf/proto"
)

var ErrMalformed = errors.New("malformed length-delimited protobuf record")
var ErrAmbiguousEOF = errors.New("ambiguous EOF in length-delimited protobuf record")
var ErrOversize = errors.New("oversized length-delimited protobuf record")

func Encode(message proto.Message, maxFrameBytes uint64) ([]byte, error) {
	payload, err := proto.MarshalOptions{Deterministic: true}.Marshal(message)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrMalformed, err)
	}
	if len(payload) == 0 {
		return nil, fmt.Errorf("%w: zero-length protobuf records are forbidden", ErrMalformed)
	}
	var prefix [binary.MaxVarintLen64]byte
	prefixSize := binary.PutUvarint(prefix[:], uint64(len(payload)))
	if maxFrameBytes == 0 || uint64(prefixSize)+uint64(len(payload)) > maxFrameBytes {
		return nil, fmt.Errorf("%w: record exceeds max_frame_bytes", ErrOversize)
	}
	result := make([]byte, prefixSize+len(payload))
	copy(result, prefix[:prefixSize])
	copy(result[prefixSize:], payload)
	return result, nil
}

func Decode(data []byte, message proto.Message, maxFrameBytes uint64) (remaining []byte, err error) {
	size, prefixSize := binary.Uvarint(data)
	if prefixSize == 0 {
		return nil, fmt.Errorf("%w: length varint is truncated", ErrAmbiguousEOF)
	}
	if prefixSize < 0 {
		return nil, fmt.Errorf("%w: length varint overflows uint64", ErrMalformed)
	}
	var canonical [binary.MaxVarintLen64]byte
	if binary.PutUvarint(canonical[:], size) != prefixSize {
		return nil, fmt.Errorf("%w: length varint is not minimally encoded", ErrMalformed)
	}
	if size == 0 {
		return nil, fmt.Errorf("%w: zero-length protobuf records are forbidden", ErrMalformed)
	}
	prefixBytes := uint64(prefixSize)
	if maxFrameBytes == 0 || prefixBytes > maxFrameBytes || size > maxFrameBytes-prefixBytes {
		return nil, fmt.Errorf("%w: record exceeds max_frame_bytes", ErrOversize)
	}
	recordSize := prefixBytes + size
	if uint64(len(data)) < recordSize {
		return nil, fmt.Errorf("%w: protobuf payload is truncated", ErrAmbiguousEOF)
	}
	if err := proto.Unmarshal(data[prefixSize:recordSize], message); err != nil {
		return nil, fmt.Errorf("%w: protobuf payload is invalid: %v", ErrMalformed, err)
	}
	return data[recordSize:], nil
}
