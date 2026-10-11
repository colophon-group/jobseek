package worker

import (
	"context"
	"encoding/base64"
	"encoding/json"
	q "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"sync"
	"testing"
)

func TestDetailFilterCompleteOriginalPublicInventories(t *testing.T) {
	dir := os.Getenv("JOBSEEK_RETAINED_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("protected public captures required")
	}
	for _, slug := range []string{"schaeffler-compact-dynamics", "vohra-physicians-corporate-careers", "drees-sommer-china", "avolta-sweden", "suez-china-infrastructure", "suez-china-operations"} {
		t.Run(slug, func(t *testing.T) {
			var c struct {
				Status    string
				Truncated bool
				Source    string `json:"source_revision"`
				Board     map[string]json.RawMessage
				Jobs      []map[string]any
				Exchanges []struct {
					Method, URL string
					RequestBody string `json:"request_body"`
					Status      int
					Body        string            `json:"body_base64"`
					Headers     map[string]string `json:"response_headers"`
				}
			}
			raw, e := os.ReadFile(filepath.Join(dir, "native1012-detail-api-"+slug+"-original-public-capture1-2026-10-11.json"))
			if e != nil || json.Unmarshal(raw, &c) != nil || c.Source != "f20f5fe83b0ba1a700bbb1d3566b5fc25b5e5b77" || c.Status != "completed-original-stream" || c.Truncated {
				t.Fatal("complete original capture invalid")
			}
			config := map[string]string{}
			for k, v := range c.Board {
				if k == "metadata" {
					config[k] = string(v)
				} else {
					var text string
					if json.Unmarshal(v, &text) != nil {
						t.Fatal("canonical binding invalid")
					}
					config[k] = text
				}
			}
			p, e := q.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
			if e != nil {
				t.Fatal("original config rejected", e)
			}
			var mu sync.Mutex
			seen := make([]bool, len(c.Exchanges))
			calls := 0
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				body, _ := io.ReadAll(r.Body)
				mu.Lock()
				index := -1
				for i, x := range c.Exchanges {
					u, err := url.Parse(x.URL)
					if err == nil && !seen[i] && r.Method == x.Method && r.Host == u.Host && r.URL.EscapedPath() == u.EscapedPath() && reflect.DeepEqual(r.URL.Query(), u.Query()) && string(body) == x.RequestBody {
						index = i
						seen[i] = true
						calls++
						break
					}
				}
				mu.Unlock()
				if index < 0 {
					t.Error("unexpected original verification request")
					w.WriteHeader(400)
					return
				}
				x := c.Exchanges[index]
				for k, v := range x.Headers {
					w.Header().Set(k, v)
				}
				data, err := base64.StdEncoding.DecodeString(x.Body)
				if err != nil {
					t.Error("capture body corrupted")
					w.WriteHeader(500)
					return
				}
				w.WriteHeader(x.Status)
				w.Write(data)
			}))
			got, e := discoverDOMInventory(context.Background(), client, p, config)
			if e != nil || got.Truncated || len(got.Jobs) != len(c.Jobs) || calls != len(c.Exchanges) {
				t.Fatal("complete original inventory or request set changed", len(got.Jobs), calls, e)
			}
			sort.Slice(got.Jobs, func(i, j int) bool { return got.Jobs[i].URL < got.Jobs[j].URL })
			sort.Slice(c.Jobs, func(i, j int) bool { return c.Jobs[i]["url"].(string) < c.Jobs[j]["url"].(string) })
			for i, j := range got.Jobs {
				if j.URL != c.Jobs[i]["url"] || !j.URLOnly {
					t.Fatal("canonical URL inventory changed")
				}
			}
			if slug == "drees-sommer-china" && got.VerifiedEmptyReason != "all discovered detail pages matched an explicit inactive state" {
				t.Fatal("explicit all-inactive proof lost")
			}
			t.Log("complete original inventory and request set match", len(got.Jobs), calls)
		})
	}
}
