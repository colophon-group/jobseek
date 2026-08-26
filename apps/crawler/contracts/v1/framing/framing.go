// Package framing implements bounded crawler.runtime/v1 unsigned-varint records.
package framing

import (
	"encoding/binary"
	"errors"
	"fmt"

	"google.golang.org/protobuf/proto"
)

var ErrFraming = errors.New("malformed length-delimited protobuf record")
var ErrFrameLimit = errors.New("length-delimited protobuf record exceeds max_frame_bytes")

func UvarintSize(value uint64) uint64 {
	size := uint64(1)
	for value >= 0x80 {
		value >>= 7
		size++
	}
	return size
}

func EncodeRecord(payload []byte, maximum uint64) ([]byte, error) {
	size := uint64(len(payload))
	prefixSize := UvarintSize(size)
	if maximum < prefixSize || size > maximum-prefixSize {
		return nil, fmt.Errorf("%w: record exceeds max_frame_bytes", ErrFrameLimit)
	}
	prefix := make([]byte, binary.MaxVarintLen64)
	written := binary.PutUvarint(prefix, size)
	result := make([]byte, written+len(payload))
	copy(result, prefix[:written])
	copy(result[written:], payload)
	return result, nil
}

func DecodeRecord(data []byte, maximum uint64) ([]byte, error) {
	size, prefixSize := binary.Uvarint(data)
	if prefixSize == 0 {
		return nil, fmt.Errorf("%w: truncated unsigned varint", ErrFraming)
	}
	if prefixSize < 0 {
		return nil, fmt.Errorf("%w: unsigned varint overflows uint64", ErrFraming)
	}
	prefixBytes := uint64(prefixSize)
	if prefixBytes != UvarintSize(size) {
		return nil, fmt.Errorf("%w: unsigned varint is not minimally encoded", ErrFraming)
	}
	if maximum < prefixBytes || size > maximum-prefixBytes {
		return nil, fmt.Errorf("%w: record exceeds max_frame_bytes", ErrFrameLimit)
	}
	recordSize := prefixBytes + size
	if uint64(len(data)) < recordSize {
		return nil, fmt.Errorf("%w: protobuf payload is truncated", ErrFraming)
	}
	if uint64(len(data)) != recordSize {
		return nil, fmt.Errorf("%w: trailing bytes after one record", ErrFraming)
	}
	return data[prefixSize:], nil
}

func Encode(message proto.Message, maximum uint64) ([]byte, error) {
	payload, err := proto.MarshalOptions{Deterministic: true}.Marshal(message)
	if err != nil {
		return nil, fmt.Errorf("%w: protobuf payload is malformed: %v", ErrFraming, err)
	}
	return EncodeRecord(payload, maximum)
}

func Decode(data []byte, message proto.Message, maximum uint64) error {
	payload, err := DecodeRecord(data, maximum)
	if err != nil {
		return err
	}
	if err := proto.Unmarshal(payload, message); err != nil {
		return fmt.Errorf("%w: protobuf payload is malformed: %v", ErrFraming, err)
	}
	return nil
}
