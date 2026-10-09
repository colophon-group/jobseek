package worker

import (
	"context"
	"encoding/json"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

// Private captures are opt-in; CI uses the frozen original-Python corpus.
func TestFeedCollisionPublicSameCapture(t *testing.T) {
	dir := os.Getenv("JOBSEEK_COLLISION_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("private same-capture evidence not selected")
	}
	names := []string{"canton-of-fribourg-main", "capgemini-frog", "mediamarktsaturn-careers-global", "chuv-careers", "swiss-post-main", "capgemini-global"}
	for _, name := range names {
		t.Run(name, func(t *testing.T) {
			var c struct {
				Canonical map[string]string
				Outcome   string
				Truncated bool
				URLs      []string                   `json:"urls"`
				JobsByURL map[string]json.RawMessage `json:"jobs_by_url"`
				Responses []struct {
					Method, URL, Body string
					RequestBody       string `json:"request_body"`
					Status            int
					Headers           map[string]string
				}
			}
			tag := "initial1"
			if name == "capgemini-global" {
				tag = "retry2"
			}
			data, err := os.ReadFile(filepath.Join(dir, "native984-collision-public-"+name+"-"+tag+"-2026-10-08.json"))
			if err != nil {
				t.Fatal("private capture absent", err)
			}
			decoder := json.NewDecoder(strings.NewReader(string(data)))
			decoder.UseNumber()
			if decoder.Decode(&c) != nil {
				t.Fatal("capture structure")
			}
			index := 0
			used := make([]bool, len(c.Responses))
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if index >= len(c.Responses) {
					t.Error("extra request outside original capture")
					w.WriteHeader(500)
					return
				}
				selected := index
				if c.Canonical["crawler_type"] == "rss" {
					selected = -1
					for i, v := range c.Responses {
						expected, _ := url.Parse(v.URL)
						if !used[i] && r.Method == v.Method && r.Host == expected.Host && r.URL.EscapedPath() == expected.EscapedPath() && reflect.DeepEqual(r.URL.Query(), expected.Query()) {
							selected = i
							break
						}
					}
					if selected < 0 {
						t.Error("request outside original captured resource set")
						w.WriteHeader(500)
						return
					}
				}
				v := c.Responses[selected]
				used[selected] = true
				index++
				expected, _ := url.Parse(v.URL)
				if r.Method != v.Method || r.Host != expected.Host || r.URL.EscapedPath() != expected.EscapedPath() || !reflect.DeepEqual(r.URL.Query(), expected.Query()) {
					t.Error("original resource or sequential request order differs")
				}
				body, _ := io.ReadAll(r.Body)
				if string(body) != v.RequestBody {
					var a, b any
					da := json.NewDecoder(strings.NewReader(string(body)))
					da.UseNumber()
					db := json.NewDecoder(strings.NewReader(v.RequestBody))
					db.UseNumber()
					if da.Decode(&a) != nil || db.Decode(&b) != nil || !reflect.DeepEqual(a, b) {
						t.Error("request body differs")
					}
				}
				for k, value := range v.Headers {
					w.Header().Set(k, value)
				}
				w.WriteHeader(v.Status)
				io.WriteString(w, v.Body)
			})
			ctx := context.Background()
			profile, err := queue.InspectRichMonitor("11111111-1111-1111-1111-111111111111", c.Canonical)
			if err != nil {
				t.Fatal("captured source configuration not admitted", err)
			}
			var found RichDiscovery
			switch profile.Provider {
			case "api_sniffer":
				found, err = discoverAPISnifferInventory(ctx, client.client, profile, c.Canonical)
			case "dom":
				found, err = discoverDOMInventory(ctx, client.client, profile, c.Canonical)
			case "rss":
				found, err = DiscoverRichMonitor(ctx, client.client, profile)
			}
			if err == nil && profile.RSSDetailEnrichment {
				found, err = enrichRSSDetailFields(ctx, client.client, profile, c.Canonical, found)
			}
			var jobs []RichMonitorJob
			if err == nil {
				jobs, err = applyFeedMonitorURLs(ctx, c.Canonical, found.Jobs)
			}
			if c.Outcome != "completed" {
				if err == nil {
					t.Fatal("original failed inventory became native success")
				}
				t.Logf("matched original failed inventory; requests=%d", index)
				return
			}
			if err != nil || found.Truncated != c.Truncated || index != len(c.Responses) {
				t.Fatal("original inventory/request terminal differs", err, index, len(c.Responses), found.Truncated, c.Truncated)
			}
			urls := []string{}
			for _, j := range jobs {
				urls = append(urls, j.URL)
			}
			sort.Strings(urls)
			if !reflect.DeepEqual(urls, c.URLs) {
				t.Fatal("canonical aliases differ", len(urls), len(c.URLs))
			}
			var cpu struct {
				Normalizer string
				Overrides  map[string]map[string]any
			}
			cpuRaw, readErr := os.ReadFile(filepath.Join(dir, "native984-collision-public-"+name+"-cpu-text-2026-10-08.json"))
			if readErr != nil || json.Unmarshal(cpuRaw, &cpu) != nil || cpu.Normalizer != "original src.processing.cpu._coerce_text applied to canonical title/description" {
				t.Fatal("original CPU text projection absent")
			}
			for _, j := range jobs {
				captured := c.JobsByURL[j.URL]
				if captured == nil {
					if !j.URLOnly {
						t.Fatal("rich Go job replaced URL-only original")
					}
					continue
				}
				var expected map[string]any
				d := json.NewDecoder(strings.NewReader(string(captured)))
				d.UseNumber()
				if d.Decode(&expected) != nil {
					t.Fatal("original rich fields")
				}
				for key, value := range cpu.Overrides[j.URL] {
					expected[key] = value
				}
				fields := map[string]any{"url": j.URL, "source_identity": j.SourceIdentity, "title": j.Title, "description": j.Description, "locations": j.Locations, "metadata": j.Metadata, "extras": j.Extras, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "language": j.Language}
				raw, _ := json.Marshal(fields)
				var got map[string]any
				d = json.NewDecoder(strings.NewReader(string(raw)))
				d.UseNumber()
				d.Decode(&got)
				for key, value := range got {
					want := expected[key]
					if key == "source_identity" && want == nil {
						want = ""
					}
					if !reflect.DeepEqual(collisionPublicComparable(value), collisionPublicComparable(want)) {
						t.Fatalf("original rich field %s differs at canonical job", key)
					}
				}
				if expected["base_salary"] != nil {
					t.Fatal("raw salary requires an explicit processor parity comparison")
				}
			}
			t.Logf("canonical URLs matched=%d requests=%d", len(urls), index)
		})
	}
}
func collisionPublicComparable(v any) any {
	switch x := v.(type) {
	case map[string]any:
		if len(x) == 0 {
			return nil
		}
		out := map[string]any{}
		for k, v := range x {
			out[k] = collisionPublicComparable(v)
		}
		return out
	case []any:
		if len(x) == 0 {
			return nil
		}
		out := make([]any, len(x))
		for i, v := range x {
			out[i] = collisionPublicComparable(v)
		}
		return out
	}
	return v
}
