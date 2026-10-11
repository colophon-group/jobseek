package worker

import (
	"context"
	"encoding/json"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

func TestDOMAutoResourcePhysicalInventoriesMatchOriginal(t *testing.T) {
	dir := os.Getenv("JOBSEEK_RETAINED_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires protected original and physical Lightpanda documents")
	}
	for _, slug := range []string{"in3-careers", "tyson-foods-thailand-mckey", "goldbeck-czech-prefabrikace", "psi-careers", "insel-gruppe-myoda", "colisee-france-onela-careers"} {
		t.Run(slug, func(t *testing.T) {
			var old struct {
				Status    string
				Truncated bool
				Source    string `json:"source_revision"`
				Board     map[string]json.RawMessage
				Jobs      []struct{ URL string }
				Errors    []any `json:"capture_errors"`
				Documents []any `json:"rendered_documents"`
			}
			b, e := os.ReadFile(filepath.Join(dir, "native1013-auto-resource-"+slug+"-original-public-capture2-2026-10-11.json"))
			if e != nil || json.Unmarshal(b, &old) != nil || old.Source != "f20f5fe83b0ba1a700bbb1d3566b5fc25b5e5b77" || old.Status != "completed-original-stream" || old.Truncated || len(old.Errors) != 0 || len(old.Documents) == 0 || len(old.Jobs) == 0 {
				t.Fatal("complete original browser capture invalid")
			}
			var engine struct {
				Engine             string
				SHA                string `json:"engine_sha256"`
				URL, HTML, Outcome string
				Final              string `json:"final_url"`
				Status             uint32
				Headers            map[string]string
			}
			b, e = os.ReadFile(filepath.Join(dir, "native1013-auto-resource-"+slug+"-lightpanda-local-public2-2026-10-11.json"))
			if e != nil || json.Unmarshal(b, &engine) != nil || engine.Engine != "Lightpanda1.0.0" || engine.SHA != "955440053a84754dd64c62f970449a56a2b350cdf43ea5f2e809a73047b8173d" || engine.Status != 200 || engine.Outcome != "document" || engine.Final == "" {
				t.Fatal("physical engine document invalid")
			}
			config := map[string]string{}
			for k, v := range old.Board {
				if k == "metadata" {
					config[k] = string(v)
				} else {
					var text string
					if json.Unmarshal(v, &text) != nil {
						t.Fatal("original canonical binding invalid")
					}
					config[k] = text
				}
			}
			if config["board_url"] != engine.URL {
				t.Fatal("physical target identity changed")
			}
			p, e := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
			if e != nil || queue.MonitorWorker(p) != queue.Browser {
				t.Fatal("original browser configuration rejected", e)
			}
			held := heldRenderedResult(engine.HTML, engine.Final, engine.Status)
			for name, value := range engine.Headers {
				switch strings.ToLower(name) {
				case "tdm-reservation":
					held.GetSuccess().ResourcePolicy.TdmReservationHeader = &value
				case "tdm-policy":
					held.GetSuccess().ResourcePolicy.TdmPolicyHeader = &value
				}
			}
			got, e := parseHeldRenderedMonitor(context.Background(), p, config, held)
			if e != nil || got.Truncated {
				t.Fatal("physical full document rejected", e)
			}
			urls := []string{}
			for _, j := range got.Jobs {
				if !j.URLOnly {
					t.Fatal("URL inventory gained unexpected rich fields")
				}
				urls = append(urls, j.URL)
			}
			expected := []string{}
			for _, j := range old.Jobs {
				expected = append(expected, j.URL)
			}
			sort.Strings(urls)
			sort.Strings(expected)
			if !reflect.DeepEqual(urls, expected) {
				t.Fatal("complete original canonical URLs changed", len(urls), len(expected))
			}
			t.Log("physical full canonical inventory matches original", len(urls))
		})
	}
}
