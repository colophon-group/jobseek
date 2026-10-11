package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestIIHFOriginalFieldsAcrossPhysicalLightpandaMirrors(t *testing.T) {
	dir := os.Getenv("JOBSEEK_FINAL_TYPES_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires protected original inventory and physical Lightpanda documents")
	}
	raw, e := os.ReadFile(filepath.Join(dir, "native1010-final-international-ice-hockey-federation-jobs-original-public-capture1-2026-10-11.json"))
	var c struct {
		Status    string
		Truncated bool
		Board     map[string]any
		Jobs      []struct {
			URL, Title, Description string
			Locations               []string
			Metadata                map[string]any
		}
	}
	if e != nil || json.Unmarshal(raw, &c) != nil || c.Status != "completed-original-stream" || c.Truncated || len(c.Jobs) != 1 {
		t.Fatal("complete original inline inventory missing")
	}
	normalized, e := os.ReadFile(filepath.Join(dir, "native1010-iihf-original-writer-normalization1-2026-10-11.json"))
	var writer struct {
		Jobs []struct{ URL, Description string }
	}
	if e != nil || json.Unmarshal(normalized, &writer) != nil || len(writer.Jobs) != 1 || writer.Jobs[0].URL != c.Jobs[0].URL {
		t.Fatal("original writer normalization missing")
	}
	c.Jobs[0].Description = writer.Jobs[0].Description
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
		t.Fatal("original browser/config binding lost", e)
	}
	o, _, e := queue.RenderedInlineMonitorOptions(config)
	if e != nil || len(o.Candidates) != 3 {
		t.Fatal("ordered original mirrors lost", e)
	}
	docs := map[string]string{}
	for i, slug := range []string{"iihf-canada", "iihf-eu", "iihf-www"} {
		raw, e := os.ReadFile(filepath.Join(dir, "native1010-"+slug+"-lightpanda-local-public1-2026-10-11.json"))
		var d struct {
			Status        int
			Outcome, HTML string
			EngineSHA256  string `json:"engine_sha256"`
		}
		if e != nil || json.Unmarshal(raw, &d) != nil || d.Status != 200 || d.Outcome != "document" || d.EngineSHA256 != "955440053a84754dd64c62f970449a56a2b350cdf43ea5f2e809a73047b8173d" {
			t.Fatal("physical latest engine document missing")
		}
		docs[o.Candidates[i].URL] = d.HTML
	}
	for first := range o.Candidates {
		t.Run(fmt.Sprint(first), func(t *testing.T) {
			calls := 0
			got, e := collectRenderedInlineCandidates(context.Background(), p, config, o, func(request queue.GreenhouseMonitorProfile, attempt int) (*runtimev1.BrowserResult, error) {
				if attempt != 0 || request.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 || request.Endpoint != o.Candidates[calls].URL {
					t.Fatal("request order/config binding changed")
				}
				calls++
				if calls <= first {
					return heldRenderedResult("unavailable", request.Endpoint, 404), nil
				}
				return heldRenderedResult(docs[request.Endpoint], request.Endpoint, 200), nil
			})
			if e != nil || got.Truncated || len(got.Jobs) != 1 || calls != first+1 {
				t.Fatalf("complete mirror inventory changed: %v jobs%d calls%d", e, len(got.Jobs), calls)
			}
			j, w := got.Jobs[0], c.Jobs[0]
			if j.URL != w.URL || j.Title == nil || *j.Title != w.Title || j.Description == nil || *j.Description != w.Description || !reflect.DeepEqual(j.Locations, w.Locations) || !reflect.DeepEqual(j.Metadata, w.Metadata) {
				t.Fatalf("original inline field changed: URL=%v title=%v description=%v description_bytes=%d/%d locations=%v metadata=%v", j.URL == w.URL, j.Title != nil && *j.Title == w.Title, j.Description != nil && *j.Description == w.Description, len(*j.Description), len(w.Description), reflect.DeepEqual(j.Locations, w.Locations), reflect.DeepEqual(j.Metadata, w.Metadata))
			}
		})
	}
}
