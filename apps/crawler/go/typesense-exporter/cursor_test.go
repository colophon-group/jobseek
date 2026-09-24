package main

import (
	"testing"
	"time"
)

func TestCursorCompatibleWithPythonState(t *testing.T) {
	for _, raw := range []string{
		"2026-09-24T18:09:00+00:00|80000000-0000-0000-0000-000000000001",
		"2026-09-24T18:09:00.123456+00:00|80000000-0000-0000-0000-000000000001",
	} {
		parsed, err := parseCursor(raw)
		if err != nil {
			t.Fatal(err)
		}
		encoded, err := parsed.encode()
		if err != nil {
			t.Fatal(err)
		}
		if encoded != raw {
			t.Fatalf("got %s, want %s", encoded, raw)
		}
	}
	legacy, err := parseCursor("2026-09-24T18:09:00+00:00")
	if err != nil || legacy.ID != zeroUUID {
		t.Fatalf("legacy cursor: %+v %v", legacy, err)
	}
	epoch, err := parseCursor("")
	if err != nil || epoch.UpdatedAt.Year() != 1 || epoch.ID != zeroUUID {
		t.Fatalf("epoch cursor: %+v %v", epoch, err)
	}
}

func TestCursorKeysetOrder(t *testing.T) {
	stamp := time.Date(2026, 9, 24, 18, 9, 0, 0, time.UTC)
	a := cursor{stamp, "00000000-0000-0000-0000-000000000001"}
	b := cursor{stamp, "00000000-0000-0000-0000-000000000002"}
	c := cursor{stamp.Add(time.Microsecond), zeroUUID}
	if !a.before(b) || !b.before(c) || c.before(a) {
		t.Fatal("keyset ordering differs from (updated_at,id)")
	}
}
