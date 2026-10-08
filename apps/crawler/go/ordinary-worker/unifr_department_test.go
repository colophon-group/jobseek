package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type unifrLinkReference struct {
	Kind, Name, Body string
	Options          api.UnifrOptions
	Output           []string
	Error            bool
}

func unifrLinkReferences(t *testing.T) []unifrLinkReference {
	t.Helper()
	var records []json.RawMessage
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_unifr_department.json")
	if err != nil || json.Unmarshal(raw, &records) != nil {
		t.Fatal("department reference unavailable", err)
	}
	out := []unifrLinkReference{}
	for _, raw := range records {
		var kind struct{ Kind string }
		if json.Unmarshal(raw, &kind) != nil {
			t.Fatal("reference kind")
		}
		if kind.Kind != "links" {
			continue
		}
		var c unifrLinkReference
		if json.Unmarshal(raw, &c) != nil {
			t.Fatal("link reference")
		}
		out = append(out, c)
	}
	if len(out) != 8 {
		t.Fatal("link corpus missing", len(out))
	}
	return out
}
func unifrLinkMetadata(c unifrLinkReference) string {
	source := "law"
	if strings.Contains(c.Options.URL, "/rsd/") {
		source = "regional-school-service"
	}
	return `{"source":"` + source + `","scraper_type":"pdf","scraper_config":{"title_source":"text"}}`
}
func unifrLinkHandler(t *testing.T, c unifrLinkReference) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if "https://"+r.Host+r.URL.Path != c.Options.URL || r.Method != "GET" {
			t.Error("unbound source")
			w.WriteHeader(400)
			return
		}
		w.Header().Set("Content-Type", "text/html")
		fmt.Fprint(w, c.Body)
	}
}

func TestUnifrHTTPPDFLinkInventoriesMatchActualPython(t *testing.T) {
	for _, c := range unifrLinkReferences(t) {
		t.Run(c.Name, func(t *testing.T) {
			client := verifiedClaimFixtureClient(t, unifrLinkHandler(t, c))
			out, err := FetchUnifrHTTP(context.Background(), client.client, queue.GreenhouseMonitorProfile{Provider: "unifr", Profile: "unifr.authoritative-items/v1", Endpoint: c.Options.URL}, map[string]string{"board_url": c.Options.URL, "metadata": unifrLinkMetadata(c), "monitor_needs_browser": "0"}, noSecondaryWait)
			if (err != nil) != c.Error || err != nil && len(out.Jobs) != 0 {
				t.Fatal(err, c.Error, out)
			}
			if c.Error {
				return
			}
			urls := []string{}
			for _, job := range out.Jobs {
				if !job.URLOnly {
					t.Fatal("PDF link acquired rich fields", job)
				}
				urls = append(urls, job.URL)
			}
			if !reflect.DeepEqual(urls, c.Output) {
				t.Fatal(urls, c.Output)
			}
		})
	}
}

func TestRealUnifrPDFLinksPreserveNativeDetailIntent(t *testing.T) {
	for _, c := range unifrLinkReferences(t) {
		if c.Error || c.Name != "law" && c.Name != "regional-rewrite" {
			continue
		}
		t.Run(c.Name, func(t *testing.T) {
			f := privateRichPipelineFixtureURL(t, "unifr", unifrLinkMetadata(c), c.Options.URL)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			client := verifiedClaimFixtureClient(t, unifrLinkHandler(t, c))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled || result.Batches.Inserted != len(c.Output) {
				t.Fatal(err, result)
			}
			assertRichDeadlineAndLease(t, f, "unifr")
			var count int
			if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid AND next_scrape_at IS NOT NULL AND is_active", f.board, f.original).Scan(&count); err != nil || count != len(c.Output) {
				t.Fatal("PDF detail intent lost", count, err)
			}
		})
	}
}
