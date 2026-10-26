package worker

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestItemListOriginalPublicChromiumAndLightpandaInventory(t *testing.T) {
	dir := os.Getenv("JOBSEEK_ITEMLIST_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("protected complete original and physical engine documents required")
	}
	raw, e := os.ReadFile(filepath.Join(dir, "miniso-original.private.json"))
	var original struct {
		ID, Status string
		Config     map[string]string
		Truncated  bool
		URLs       []string
		Documents  []struct{ HTML string }
	}
	if e != nil || json.Unmarshal(raw, &original) != nil || original.Status != "complete" || original.Truncated || len(original.URLs) == 0 || len(original.Documents) != 1 {
		t.Fatal("complete original browser reference required")
	}
	engine := os.Getenv("JOBSEEK_PUBLIC_LIGHTPANDA_REFERENCE")
	if engine == "" {
		engine = "1.0.2"
	}
	physicalFile, expectedSHA := "miniso-lightpanda102-1.private.json", "9f3ee749a55d1abc5f8fdda0d9559c3b2abcec5bff5bfe902bc06c0a89538ec4"
	if engine == "1.0.1" {
		physicalFile, expectedSHA = "miniso-lightpanda101-1.private.json", "d4b66ac8484f49d7a49e05b6f9342716c747129527fcbe8ffe36679ffa18dd60"
	} else if engine != "1.0.2" {
		t.Fatal("unsupported official engine reference")
	}
	raw, e = os.ReadFile(filepath.Join(dir, physicalFile))
	var physical struct {
		Complete     bool
		Status       int
		HTML         string
		EngineSHA256 string `json:"engine_sha256"`
	}
	if e != nil || json.Unmarshal(raw, &physical) != nil || !physical.Complete || physical.Status != 200 || physical.EngineSHA256 != expectedSHA {
		t.Fatal("verified physical1.0.2 document required")
	}
	p, e := queue.InspectRichMonitor(original.ID, original.Config)
	if e != nil || p.Profile != "nextdata.rendered-urls/v1" {
		t.Fatal("actual canonical rendered URL profile rejected")
	}
	options, _, e := queue.RenderedNextdataMonitorOptions(original.Config)
	if e != nil {
		t.Fatal("actual held options rejected")
	}
	for name, source := range map[string]string{"physical-lightpanda-" + engine: physical.HTML} {
		t.Run(name, func(t *testing.T) {
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("held document issued extra HTTP") })
			actual := []string{}
			result, e := discoverNextdataWithPages(context.Background(), client.client, p, original.Config, func(jobs []RichMonitorJob) error {
				for _, j := range jobs {
					actual = append(actual, j.URL)
				}
				return nil
			}, func(ctx context.Context, endpoint string) nextdataPage {
				return parseHeldNextdataPage(ctx, p, options, heldRenderedResult(source, endpoint, 200))
			})
			sort.Strings(actual)
			sort.Strings(original.URLs)
			if e != nil || result.Truncated || !reflect.DeepEqual(actual, original.URLs) {
				t.Fatal("original complete public URL inventory changed", e != nil, len(actual), len(original.URLs))
			}
			t.Logf("original complete %d URLs matched", len(actual))
		})
	}
}
