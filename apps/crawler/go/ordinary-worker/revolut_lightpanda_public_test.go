package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRevolutOriginalBrowserFieldsThroughLightpandaDocument(t *testing.T) {
	dir := os.Getenv("JOBSEEK_FINAL_TYPES_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires protected original complete Revolut capture")
	}
	raw, e := os.ReadFile(filepath.Join(dir, "native1010-final-revolut-careers-original-public-capture1-2026-10-11.json"))
	var c struct {
		Status    string
		Truncated bool
		Board     map[string]any
		Jobs      []struct {
			URL, Title string
			Locations  []string
			Metadata   map[string]any
		}
		Exchanges []struct {
			URL     string
			Status  int
			Body    string            `json:"body_base64"`
			Headers map[string]string `json:"response_headers"`
		}
	}
	if e != nil || json.Unmarshal(raw, &c) != nil || c.Status != "completed-original-stream" || c.Truncated || len(c.Jobs) != 369 {
		t.Fatal("complete original369-job inventory missing")
	}
	config := map[string]string{}
	for k, v := range c.Board {
		if k == "metadata" {
			b, _ := json.Marshal(v)
			config[k] = string(b)
		} else {
			config[k] = fmt.Sprint(v)
		}
	}
	p, e := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
	if e != nil || queue.MonitorWorker(p) != queue.Browser {
		t.Fatal("original browser queue binding lost", e)
	}
	physicalRaw, e := os.ReadFile(filepath.Join(dir, "native1010-revolut-lightpanda-local-public1-2026-10-11.json"))
	var physical struct {
		Status        int
		Outcome, HTML string
		EngineSHA256  string `json:"engine_sha256"`
	}
	if e != nil || json.Unmarshal(physicalRaw, &physical) != nil || physical.Status != 200 || physical.Outcome != "document" || physical.EngineSHA256 != "955440053a84754dd64c62f970449a56a2b350cdf43ea5f2e809a73047b8173d" {
		t.Fatal("verified physical latest Lightpanda document required")
	}
	options, navigation, e := queue.RenderedNextdataMonitorOptions(config)
	if e != nil || !options.Strict || navigation["stealth"] != nil {
		t.Fatal("source-bound Lightpanda compatibility rejected", e)
	}
	check := func(client *http.Client) {
		ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		defer cancel()
		jobs := []RichMonitorJob{}
		got, e := discoverNextdataWithPages(ctx, client, p, config, func(chunk []RichMonitorJob) error { jobs = append(jobs, chunk...); return nil }, func(ctx context.Context, endpoint string) nextdataPage {
			if endpoint != p.Endpoint {
				t.Fatal("unexpected resource")
			}
			return parseHeldNextdataPage(ctx, p, options, heldRenderedResult(physical.HTML, endpoint, 200))
		})
		if e != nil || got.Truncated || len(jobs) != len(c.Jobs) {
			t.Fatalf("original complete rendered inventory differs: %v jobs%d/%d", e, len(jobs), len(c.Jobs))
		}
		sort.Slice(jobs, func(i, j int) bool { return jobs[i].URL < jobs[j].URL })
		sort.Slice(c.Jobs, func(i, j int) bool { return c.Jobs[i].URL < c.Jobs[j].URL })
		for i, want := range c.Jobs {
			j := jobs[i]
			if j.URL != want.URL || j.Title == nil || *j.Title != strings.TrimSpace(want.Title) || !reflect.DeepEqual(j.Locations, want.Locations) || !reflect.DeepEqual(j.Metadata, want.Metadata) {
				t.Fatalf("original field differs at index%d", i)
			}
		}
	}
	check(&http.Client{Transport: testRoundTripper(func(*http.Request) (*http.Response, error) {
		t.Error("rendered path used Go HTTP")
		return nil, queue.ErrConfiguration
	})})
}
