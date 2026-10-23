package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

type impactTestFeed struct {
	body     []byte
	status   int
	requests []string
}

func (f *impactTestFeed) Do(req *http.Request) (*http.Response, error) {
	f.requests = append(f.requests, req.URL.String())
	status := f.status
	if status == 0 {
		status = 200
	}
	return &http.Response{StatusCode: status, Header: http.Header{}, Body: io.NopCloser(bytes.NewReader(f.body)), Request: req}, nil
}
func TestImpactCacheMatchesFrozenPython(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_impact_cache.json")
	if e != nil {
		t.Fatal(e)
	}
	var oracle struct {
		Manifest map[string]any
		Rows     []inventoryRow
		Sequence []struct {
			Phase           string
			Report, Pointer map[string]any
			FamilySHA       string `json:"family_summary_sha256"`
		}
		Requests []string
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if e = decoder.Decode(&oracle); e != nil {
		t.Fatal(e)
	}
	body, e := os.ReadFile("testdata/python-impact-strings.parquet")
	if e != nil {
		t.Fatal(e)
	}
	feed := &impactTestFeed{body: body}
	opts := defaultImpactCacheOptions()
	opts.MinFreeBytes = 0
	c, e := newImpactCache(t.TempDir(), feed, opts)
	if e != nil {
		t.Fatal(e)
	}
	c.Now = func() time.Time { return time.Date(2026, 10, 17, 21, 45, 0, 0, time.UTC) }
	digest := sha256Bytes(body)
	familyPath := filepath.Join(c.CacheDir, "families", "greenhouse", digest+".json")
	for _, step := range oracle.Sequence {
		t.Run(step.Phase, func(t *testing.T) {
			if step.Phase == "recovered" {
				previous, e := c.loadCurrent(false, 0)
				if e != nil {
					t.Fatal(e)
				}
				for _, p := range []string{filepath.Join(c.CacheDir, "snapshots", previous.SnapshotSHA256+".json"), familyPath} {
					if e = os.WriteFile(p, []byte("corrupt"), 0600); e != nil {
						t.Fatal(e)
					}
				}
			}
			sourceHash := strings.Repeat("a", 64)
			if step.Phase == "manifest_changed" {
				sourceHash = strings.Repeat("c", 64)
			}
			input := inventorySnapshot{ManifestSHA256: sourceHash, InventorySHA256: strings.Repeat("b", 64), GeneratedAt: "2026-10-01T12:00:00+00:00", Manifest: oracle.Manifest, Rows: oracle.Rows}
			got, e := c.Sync(context.Background(), input)
			if e != nil {
				t.Fatal(e)
			}
			if !reflect.DeepEqual(normalizedJSON(t, got.report()), normalizedJSON(t, step.Report)) {
				t.Fatalf("impact cache report differs from Python\ngot %#v\nwant %#v", got.report(), step.Report)
			}
			raw, e := os.ReadFile(c.currentPath())
			if e != nil {
				t.Fatal(e)
			}
			var pointer any
			if e = json.Unmarshal(raw, &pointer); e != nil {
				t.Fatal(e)
			}
			if !reflect.DeepEqual(pointer, normalizedJSON(t, step.Pointer)) {
				t.Fatal("current pointer differs from Python")
			}
			raw, e = os.ReadFile(familyPath)
			if e != nil || sha256Bytes(raw) != step.FamilySHA {
				t.Fatal("serialized family cache differs from Python")
			}
		})
	}
	if !reflect.DeepEqual(feed.requests, oracle.Requests) {
		t.Fatal("artifact cache request reuse differs from Python")
	}
	before, e := os.ReadFile(c.currentPath())
	if e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(familyPath, []byte("corrupt"), 0600); e != nil {
		t.Fatal(e)
	}
	feed.status = 503
	input := inventorySnapshot{ManifestSHA256: strings.Repeat("d", 64), InventorySHA256: strings.Repeat("b", 64), GeneratedAt: "2026-10-01T12:00:00+00:00", Manifest: oracle.Manifest, Rows: oracle.Rows}
	if _, e = c.Sync(context.Background(), input); e == nil {
		t.Fatal("failed refresh accepted")
	}
	after, e := os.ReadFile(c.currentPath())
	if e != nil || string(before) != string(after) {
		t.Fatal("rejected impact refresh changed last-known-good")
	}
	if _, e = c.loadCurrent(false, 0); e != nil {
		t.Fatal("last-known-good snapshot unreadable", e)
	}
}
