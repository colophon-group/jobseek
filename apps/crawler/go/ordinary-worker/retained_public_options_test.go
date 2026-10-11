package worker

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Private, complete original captures are replayed without origin requests.
// Public payloads remain outside Git; CI exercises synthetic guards separately.
func TestRetainedDOMAndAPIOptionsMatchCompleteOriginalPublicInventories(t *testing.T) {
	directory := os.Getenv("JOBSEEK_RETAINED_PUBLIC_CAPTURE_DIR")
	if directory == "" {
		t.Skip("requires protected original complete public captures")
	}
	for _, slug := range []string{"patrimonium-careers", "github-careers", "howden-denmark-elvium"} {
		t.Run(slug, func(t *testing.T) {
			raw, err := os.ReadFile(filepath.Join(directory, "native1012-public-options-"+slug+"-original-public-capture1-2026-10-11.json"))
			if err != nil {
				t.Fatal("original capture unavailable")
			}
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
			if json.Unmarshal(raw, &c) != nil || c.Source != "f20f5fe83b0ba1a700bbb1d3566b5fc25b5e5b77" || c.Status != "completed-original-stream" || c.Truncated || len(c.Jobs) == 0 || len(c.Exchanges) == 0 {
				t.Fatal("complete original inventory invalid")
			}
			config := map[string]string{}
			for k, v := range c.Board {
				if k == "metadata" {
					config[k] = string(v)
				} else {
					var text string
					if json.Unmarshal(v, &text) != nil {
						t.Fatal("canonical config invalid")
					}
					config[k] = text
				}
			}
			profile, err := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
			if err != nil || queue.MonitorWorker(profile) != queue.Simple {
				t.Fatal("original HTTP monitor config rejected", err)
			}
			calls := 0
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if calls >= len(c.Exchanges) {
					t.Error("extra request beyond original complete stream")
					w.WriteHeader(400)
					return
				}
				x := c.Exchanges[calls]
				calls++
				body, _ := io.ReadAll(r.Body)
				expectedURL, parseErr := url.Parse(x.URL)
				if parseErr != nil || r.Method != x.Method || expectedURL.Scheme != "https" || r.Host != expectedURL.Host || r.URL.EscapedPath() != expectedURL.EscapedPath() || !reflect.DeepEqual(r.URL.Query(), expectedURL.Query()) || string(body) != x.RequestBody {
					t.Error("original request sequence or query semantics changed")
					w.WriteHeader(400)
					return
				}
				for k, v := range x.Headers {
					w.Header().Set(k, v)
				}
				data, err := base64.StdEncoding.DecodeString(x.Body)
				if err != nil {
					t.Error("original response corrupted")
					w.WriteHeader(500)
					return
				}
				w.WriteHeader(x.Status)
				w.Write(data)
			}))
			var got RichDiscovery
			if config["crawler_type"] == "dom" {
				got, err = discoverDOMInventory(context.Background(), client, profile, config)
			} else {
				got, err = discoverAPISnifferInventory(context.Background(), client, profile, config)
			}
			if err != nil || got.Truncated || len(got.Jobs) != len(c.Jobs) || calls != len(c.Exchanges) {
				t.Fatal("complete inventory or request count changed", len(got.Jobs), calls, err)
			}
			sort.Slice(got.Jobs, func(i, j int) bool { return got.Jobs[i].URL < got.Jobs[j].URL })
			sort.Slice(c.Jobs, func(i, j int) bool { return c.Jobs[i]["url"].(string) < c.Jobs[j]["url"].(string) })
			for i, j := range got.Jobs {
				actual := map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "date_posted": j.DatePosted, "metadata": j.Metadata, "extras": j.Extras}
				b, _ := json.Marshal(actual)
				json.Unmarshal(b, &actual)
				for k, want := range c.Jobs[i] {
					if !reflect.DeepEqual(actual[k], want) {
						t.Fatal("original canonical field changed", i, k)
					}
				}
				if config["crawler_type"] == "dom" && !j.URLOnly {
					t.Fatal("URL-only monitor gained rich data")
				}
			}
			t.Log("complete original fields and request sequence match", len(got.Jobs), calls)
		})
	}
}
