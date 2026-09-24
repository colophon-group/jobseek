package main

import (
	"errors"
	"fmt"
	"strings"
	"time"
)

const zeroUUID = "00000000-0000-0000-0000-000000000000"

// cursor is the durable (updated_at, UUID) keyset position already stored in
// exporter_state under last_export_ts:typesense:job_posting. The Go exporter
// must reuse that position when Python is quiesced, never start a new stream.
type cursor struct {
	UpdatedAt time.Time
	ID        string
}

func parseCursor(value string) (cursor, error) {
	if value == "" {
		return cursor{UpdatedAt: time.Date(1, 1, 1, 0, 0, 0, 0, time.UTC), ID: zeroUUID}, nil
	}
	parts := strings.SplitN(value, "|", 2)
	stamp, err := time.Parse(time.RFC3339Nano, parts[0])
	if err != nil {
		return cursor{}, fmt.Errorf("parse Typesense cursor timestamp: %w", err)
	}
	id := zeroUUID
	if len(parts) == 2 {
		id = parts[1]
	}
	if _, _, _, _, err := candidateOrder(id, false); err != nil {
		return cursor{}, fmt.Errorf("parse Typesense cursor UUID: %w", err)
	}
	return cursor{UpdatedAt: stamp, ID: id}, nil
}

func (c cursor) encode() (string, error) {
	if c.UpdatedAt.IsZero() {
		return "", errors.New("Typesense cursor timestamp is zero")
	}
	if _, _, _, _, err := candidateOrder(c.ID, false); err != nil {
		return "", err
	}
	// asyncpg/PostgreSQL timestamps have microsecond precision; Python's
	// datetime.isoformat emits six fractional digits only when nonzero.
	stamp := c.UpdatedAt.In(time.UTC).Truncate(time.Microsecond)
	format := "2006-01-02T15:04:05.000000+00:00"
	if stamp.Nanosecond() == 0 {
		format = "2006-01-02T15:04:05+00:00"
	}
	return stamp.Format(format) + "|" + c.ID, nil
}

func (c cursor) before(next cursor) bool {
	if c.UpdatedAt.Before(next.UpdatedAt) {
		return true
	}
	if c.UpdatedAt.After(next.UpdatedAt) {
		return false
	}
	return c.ID < next.ID
}
