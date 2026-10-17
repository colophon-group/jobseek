package main

import (
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

type sourceOracle struct {
	Fixtures []struct{ Manifest, CSV, ETag string }
	Sequence []struct {
		Phase  string
		Report map[string]any
		Rows   []inventoryRow
	}
	Requests []map[string]string
}
type sourceFeed struct {
	oracle             sourceOracle
	generation, status int
	requests           []map[string]string
}

func (f *sourceFeed) Do(req *http.Request) (*http.Response, error) {
	item := f.oracle.Fixtures[f.generation]
	f.requests = append(f.requests, map[string]string{"url": req.URL.String(), "conditional": req.Header.Get("If-None-Match")})
	status, body := 200, item.CSV
	headers := http.Header{}
	if req.URL.String() == defaultManifestURL {
		body = item.Manifest
		headers.Set("ETag", item.ETag)
		if req.Header.Get("If-None-Match") == item.ETag {
			status = 304
			body = ""
		}
	} else if f.status != 0 {
		status = f.status
	}
	return &http.Response{StatusCode: status, Header: headers, Body: io.NopCloser(strings.NewReader(body)), Request: req}, nil
}
func loadSourceOracle(t *testing.T) sourceOracle {
	t.Helper()
	body, e := os.ReadFile("testdata/python_source.json")
	var oracle sourceOracle
	if e != nil || json.Unmarshal(body, &oracle) != nil {
		t.Fatal("read source oracle", e)
	}
	return oracle
}
func newTestSource(t *testing.T) (*inventorySource, *sourceFeed) {
	t.Helper()
	feed := &sourceFeed{oracle: loadSourceOracle(t)}
	s, e := newInventorySource(t.TempDir(), feed, defaultManifestURL, defaultInventorySourceOptions())
	if e != nil {
		t.Fatal(e)
	}
	s.Now = func() time.Time { return time.Date(2026, 10, 17, 21, 45, 0, 0, time.UTC) }
	return s, feed
}
func normalizedJSON(t *testing.T, value any) any {
	t.Helper()
	body, e := json.Marshal(value)
	if e != nil {
		t.Fatal(e)
	}
	var out any
	if e = json.Unmarshal(body, &out); e != nil {
		t.Fatal(e)
	}
	return out
}
func TestSourceMatchesPythonRefreshRecoveryAndChange(t *testing.T) {
	s, feed := newTestSource(t)
	for _, step := range feed.oracle.Sequence {
		t.Run(step.Phase, func(t *testing.T) {
			if step.Phase == "recovery" {
				p, _ := s.objectPath(sha256Bytes([]byte(feed.oracle.Fixtures[0].CSV)))
				if e := os.WriteFile(p, []byte("corrupt"), 0600); e != nil {
					t.Fatal(e)
				}
			}
			if step.Phase == "changed" {
				feed.generation = 1
			}
			got, e := s.Sync(context.Background())
			if e != nil {
				t.Fatal(e)
			}
			if !reflect.DeepEqual(normalizedJSON(t, got.report()), normalizedJSON(t, step.Report)) {
				t.Fatalf("report differs from frozen Python oracle\ngot: %#v\nwant: %#v", got.report(), step.Report)
			}
			if !reflect.DeepEqual(got.Rows, step.Rows) {
				t.Fatal("validated inventory rows differ from Python")
			}
		})
	}
	if !reflect.DeepEqual(feed.requests, feed.oracle.Requests) {
		t.Fatal("conditional/recovery HTTP requests differ from Python")
	}
}
func TestSourceRetainsLastKnownGoodOnRejectedUpdate(t *testing.T) {
	for _, mode := range []string{"older", "bad-checksum", "bad-status", "shrink"} {
		t.Run(mode, func(t *testing.T) {
			s, feed := newTestSource(t)
			if _, e := s.Sync(context.Background()); e != nil {
				t.Fatal(e)
			}
			before, e := os.ReadFile(s.currentPath())
			if e != nil {
				t.Fatal(e)
			}
			feed.generation = 1
			switch mode {
			case "older":
				feed.oracle.Fixtures[1].Manifest = strings.ReplaceAll(feed.oracle.Fixtures[1].Manifest, "2026-10-02", "2026-09-01")
			case "bad-checksum":
				feed.oracle.Fixtures[1].CSV += "corrupt"
			case "bad-status":
				feed.status = 503
			case "shrink":
				var m map[string]any
				if e := json.Unmarshal([]byte(feed.oracle.Fixtures[1].Manifest), &m); e != nil {
					t.Fatal(e)
				}
				lines := strings.Split(feed.oracle.Fixtures[0].CSV, "\n")
				body := lines[0] + "\n" + lines[1] + "\n"
				m["companies"].(map[string]any)["rows"] = 1
				m["companies"].(map[string]any)["sha256"] = sha256Bytes([]byte(body))
				m["companies"].(map[string]any)["size_bytes"] = len(body)
				m["stats"].(map[string]any)["total_companies"] = 1
				m["by_ats_companies"] = map[string]any{"greenhouse": m["by_ats_companies"].(map[string]any)["greenhouse"]}
				raw, e := json.Marshal(m)
				if e != nil {
					t.Fatal(e)
				}
				feed.oracle.Fixtures[1].CSV = body
				feed.oracle.Fixtures[1].Manifest = string(raw)

			}
			if _, e = s.Sync(context.Background()); e == nil {
				t.Fatal("rejected update accepted")
			}
			after, e := os.ReadFile(s.currentPath())
			if e != nil || string(before) != string(after) {
				t.Fatal("last-known-good pointer changed")
			}
			if _, e = s.loadCurrent(); e != nil {
				t.Fatal("last-known-good unreadable", e)
			}
		})
	}
}
func TestSource304PrunesOrphansAndLockExcludesSecondRun(t *testing.T) {
	s, _ := newTestSource(t)
	if _, e := s.Sync(context.Background()); e != nil {
		t.Fatal(e)
	}
	paths := []string{filepath.Join(s.CacheDir, "objects", strings.Repeat("f", 64)), filepath.Join(s.CacheDir, ".current.json.crash"), filepath.Join(s.CacheDir, "snapshots", ".snapshot.json.crash")}
	for _, p := range paths {
		if e := os.WriteFile(p, []byte("orphan"), 0600); e != nil {
			t.Fatal(e)
		}
	}
	if _, e := s.Sync(context.Background()); e != nil {
		t.Fatal(e)
	}
	for _, p := range paths {
		if _, e := os.Stat(p); !os.IsNotExist(e) {
			t.Fatal("orphan retained")
		}
	}
	unlock, e := acquireInventoryLock(filepath.Join(s.CacheDir, "source.lock"))
	if e != nil {
		t.Fatal(e)
	}
	defer unlock()
	if _, e := s.Sync(context.Background()); e == nil {
		t.Fatal("concurrent source run admitted")
	}
}
func TestInventoryURLTrustAndIdentity(t *testing.T) {
	for _, u := range []string{"https://storage.stapply.ai/jobhive/v1/manifest.json", "https://storage.stapply.ai:00443/jobhive/v1/manifest.json"} {
		if e := trustedArtifactURL(u); e != nil {
			t.Fatal("Python-compatible trusted URL refused", e)
		}
	}
	for _, u := range []string{"https://user@storage.stapply.ai/jobhive/v1/a", "https://storage.stapply.ai:444/jobhive/v1/a", "http://storage.stapply.ai/jobhive/v1/a", "https://example.test/jobhive/v1/a", "https://storage.stapply.ai/other/a", "https://storage.stapply.ai/jobhive/v1/a#x"} {
		if trustedArtifactURL(u) == nil {
			t.Fatal("untrusted URL admitted")
		}
	}
	a, e := normalizedInventoryURL("https://EXAMPLE.test:00443/A%2Fb/?x=1")
	if e != nil || a != "https://example.test/A%2Fb?x=1" {
		t.Fatalf("normalized identity differs: %q %v", a, e)
	}
}
