package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestPOSTRefreshOriginalPublicRequestSessionAndEmptyProof(t *testing.T) {
	dir := os.Getenv("JOBSEEK_POST_REFRESH_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires protected original complete publisher capture")
	}
	raw, e := os.ReadFile(filepath.Join(dir, "native1006-post-refresh-credit-agricole-next-bank-careers-original-public-capture1-2026-10-10.json"))
	if e != nil {
		t.Fatal("original publisher capture unavailable")
	}
	var c struct {
		Board     map[string]any
		Jobs      []struct{ URL string }
		Exchanges []lastHTTPExchange
		Status    string
		Truncated bool
	}
	if json.NewDecoder(bytes.NewReader(raw)).Decode(&c) != nil || c.Status != "complete" || c.Truncated || len(c.Jobs) != 0 || len(c.Exchanges) != 2 {
		t.Fatal("original complete empty publisher result unavailable")
	}
	config := map[string]string{}
	for k, v := range c.Board {
		if k == "metadata" {
			b, e := json.Marshal(v)
			if e != nil {
				t.Fatal(e)
			}
			config[k] = string(b)
		} else {
			config[k] = fmt.Sprint(v)
		}
	}
	p, e := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
	if e != nil {
		t.Fatal("canonical refreshed source rejected", e)
	}
	calls := 0
	client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		i := calls
		calls++
		if i >= len(c.Exchanges) {
			t.Error("extra publisher request")
			w.WriteHeader(400)
			return
		}
		x := c.Exchanges[i]
		body, _ := io.ReadAll(r.Body)
		if r.Method != x.Method || "https://"+r.Host+r.URL.String() != x.URL || string(body) != x.RequestBody || r.Header.Get("Cookie") != x.Headers["cookie"] {
			t.Error("original bootstrap/POST/session request changed")
			w.WriteHeader(400)
			return
		}
		for k, v := range x.ResponseHeaders {
			w.Header().Set(k, v)
		}
		for _, v := range x.SetCookies {
			w.Header().Add("Set-Cookie", v)
		}
		w.WriteHeader(x.Status)
		fmt.Fprint(w, x.Body)
	}))
	found, e := discoverAPISnifferInventory(context.Background(), client, p, config)
	if e != nil || len(found.Jobs) != 0 || found.Truncated || calls != 2 {
		t.Fatal("original complete empty proof changed", e, calls)
	}
	t.Log("original fresh public page and nonce/cookie-bound POST request match exactly; original explicit empty result preserved")
}
