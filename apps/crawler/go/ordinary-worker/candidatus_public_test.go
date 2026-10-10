package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestCandidatusCompleteOriginalPublicPostbacks(t *testing.T) {
	dir := os.Getenv("JOBSEEK_CANDIDATUS_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires complete original browser and public form captures")
	}
	read := func(name string, target any) {
		body, e := os.ReadFile(filepath.Join(dir, name))
		if e != nil || json.Unmarshal(body, target) != nil {
			t.Fatal("protected complete capture unavailable")
		}
	}
	var original struct {
		Board  map[string]any
		Jobs   []struct{ URL string }
		Status string
	}
	read("native1008-candidatus-fresenius-kabi-france-original-public-capture1-2026-10-10.json", &original)
	var captured struct {
		Trace []struct {
			Method, URL, Body string
			Status            int
			RequestBody       string            `json:"request_body"`
			ResponseHeaders   map[string]string `json:"response_headers"`
		}
	}
	read("native1008-candidatus-public-http-forms-diagnostic1-2026-10-10.json", &captured)
	if original.Status != "complete" || len(original.Jobs) != 15 || len(captured.Trace) != 30 {
		t.Fatal("complete original inventory unavailable")
	}
	config := map[string]string{}
	for k, v := range original.Board {
		if k == "metadata" {
			body, _ := json.Marshal(v)
			config[k] = string(body)
		} else {
			config[k] = fmt.Sprint(v)
		}
	}
	p, e := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
	if e != nil || p.Profile != queue.CandidatusHTTPProfile || queue.MonitorWorker(p) != queue.Browser {
		t.Fatal("canonical postback profile not bound", e)
	}
	calls := 0
	client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls >= len(captured.Trace) {
			t.Error("extra postback request")
			w.WriteHeader(400)
			return
		}
		x := captured.Trace[calls]
		calls++
		if r.Method != x.Method || "https://"+r.Host+r.URL.String() != x.URL {
			t.Error("original form URL or method changed")
		}
		body, e := io.ReadAll(r.Body)
		if e != nil || string(body) != x.RequestBody {
			t.Error("fresh original form field order or values changed")
		}
		for k, v := range x.ResponseHeaders {
			w.Header().Set(k, v)
		}
		w.WriteHeader(x.Status)
		fmt.Fprint(w, x.Body)
	}))
	result, e := discoverCandidatusHTTP(context.Background(), client, config)
	if e != nil || result.Truncated || calls != 30 {
		t.Fatal("complete postback session failed", e, calls)
	}
	got, want := []string{}, []string{}
	for _, j := range result.Jobs {
		if !j.URLOnly {
			t.Fatal("URL-only inventory acquired rich fields")
		}
		got = append(got, j.URL)
	}
	for _, j := range original.Jobs {
		want = append(want, j.URL)
	}
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatal("all original canonical identities not preserved", len(got), len(want))
	}
	t.Log("all15 original browser URLs and all30 published form requests match")
}

func TestCandidatusLivePublicHTTP1Inventory(t *testing.T) {
	if os.Getenv("JOBSEEK_CANDIDATUS_LIVE_PUBLIC") != "1" {
		t.Skip("requires explicit bounded live public qualification")
	}
	dir := os.Getenv("JOBSEEK_CANDIDATUS_PUBLIC_CAPTURE_DIR")
	raw, e := os.ReadFile(filepath.Join(dir, "native1008-candidatus-fresenius-kabi-france-original-public-capture1-2026-10-10.json"))
	if e != nil {
		t.Fatal("complete original capture unavailable")
	}
	var original struct {
		Board  map[string]any
		Jobs   []struct{ URL string }
		Status string
	}
	if json.Unmarshal(raw, &original) != nil || original.Status != "complete" || len(original.Jobs) != 15 {
		t.Fatal("complete original reference unavailable")
	}
	config := map[string]string{}
	for k, v := range original.Board {
		if k == "metadata" {
			body, _ := json.Marshal(v)
			config[k] = string(body)
		} else {
			config[k] = fmt.Sprint(v)
		}
	}
	bundle, e := os.ReadFile(os.Getenv("JOBSEEK_CANDIDATUS_CA_FILE"))
	if e != nil {
		t.Fatal("pinned public CA unavailable")
	}
	hash := sha256.Sum256(bundle)
	if hex.EncodeToString(hash[:]) != pinnedCASHA256 {
		t.Fatal("public CA pin differs")
	}
	client, e := NewVerifiedDirectHTTP(DirectHTTPConfig{CABundlePEM: bundle})
	if e != nil {
		t.Fatal(e)
	}
	defer client.CloseIdleConnections()
	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()
	result, e := discoverCandidatusHTTP(ctx, client.client, config)
	if e != nil || result.Truncated {
		t.Fatal("live verified Go HTTP1 session failed", e)
	}
	got, want := []string{}, []string{}
	for _, j := range result.Jobs {
		got = append(got, j.URL)
	}
	for _, j := range original.Jobs {
		want = append(want, j.URL)
	}
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatal("live original inventory changed", len(got), len(want))
	}
	t.Log("actual verified Go HTTP1 public session matches all15 original URLs")
}
