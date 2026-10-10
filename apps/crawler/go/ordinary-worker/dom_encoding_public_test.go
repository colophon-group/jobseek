package worker

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestDOMJapaneseEncodingOriginalPublicInventories(t *testing.T) {
	dir := os.Getenv("JOBSEEK_DOM_ENCODING_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires protected original encoded public inventories")
	}
	for _, slug := range []string{"bridgestone-careers-japan", "valeo-careers-jp"} {
		t.Run(slug, func(t *testing.T) {
			raw, e := os.ReadFile(filepath.Join(dir, "native1006-dom-encoding-"+slug+"-original-public-capture1-2026-10-10.json"))
			if e != nil {
				t.Fatal("original encoded capture unavailable")
			}
			var c struct {
				Board     map[string]any
				Jobs      []struct{ URL string }
				Exchanges []struct {
					Method, URL     string
					Status          int
					BodyBase64      string            `json:"body_base64"`
					ResponseHeaders map[string]string `json:"response_headers"`
				}
				Status string
			}
			if json.NewDecoder(bytes.NewReader(raw)).Decode(&c) != nil || c.Status != "complete" || len(c.Exchanges) != 1 {
				t.Fatal("complete original source unavailable")
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
			calls := 0
			x := c.Exchanges[0]
			body, e := base64.StdEncoding.DecodeString(x.BodyBase64)
			if e != nil {
				t.Fatal("original encoded response unavailable")
			}
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if calls != 1 || r.Method != x.Method || "https://"+r.Host+r.URL.String() != x.URL {
					t.Error("original encoded source request changed")
					w.WriteHeader(400)
					return
				}
				for k, v := range x.ResponseHeaders {
					w.Header().Set(k, v)
				}
				w.WriteHeader(x.Status)
				_, _ = w.Write(body)
			}))
			profile, e := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
			if e != nil {
				t.Fatal("canonical encoded monitor rejected", e)
			}
			inventory, e := discoverDOMInventory(context.Background(), client, profile, config)
			if e != nil || calls != 1 || inventory.Truncated {
				t.Fatal("original encoded inventory changed", e)
			}
			got, want := []string{}, []string{}
			for _, j := range inventory.Jobs {
				if !j.URLOnly {
					t.Fatal("URL-only source acquired unsolicited rich fields")
				}
				got = append(got, j.URL)
			}
			for _, j := range c.Jobs {
				want = append(want, j.URL)
			}
			sort.Strings(got)
			sort.Strings(want)
			if !reflect.DeepEqual(got, want) {
				t.Fatal("original encoded inventory identities changed", len(got), len(want))
			}
			t.Log("original encoded inventory URLs and request match", len(got))
		})
	}
}
