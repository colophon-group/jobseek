package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestInlineDiscoveryMatchesActualPythonInventories(t *testing.T) {
	body, err := os.ReadFile("../api-sniffer-monitor/testdata/python_inline_inventory.json")
	var corpus struct {
		Cases []struct {
			Now                 string
			Name, HTML          string
			BoardURL            string `json:"board_url"`
			Metadata            json.RawMessage
			Error, Truncated    bool
			Jobs                []any  `json:"worker_jobs"`
			VerifiedEmptyReason string `json:"verified_empty_reason"`
		}
	}
	if err != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) != 51 {
		t.Fatal("actual Inline corpus missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			requests := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests++
				if "https://"+r.Host+r.URL.String() != c.BoardURL {
					t.Error("undeclared request")
				}
				fmt.Fprint(w, c.HTML)
			}))
			config := map[string]string{"crawler_type": "inline", "monitor_needs_browser": "0", "board_url": c.BoardURL, "metadata": string(c.Metadata)}
			profile := queue.GreenhouseMonitorProfile{Provider: "inline", Profile: "inline.document-items/v1", Endpoint: c.BoardURL}
			now, err := time.Parse(time.RFC3339Nano, c.Now)
			if err != nil {
				t.Fatal("frozen clock missing")
			}
			got, err := discoverInlineInventoryAt(context.Background(), client.client, profile, config, now)
			if (err != nil) != c.Error {
				t.Fatal("native Inline failure differs", err)
			}
			if err != nil {
				return
			}
			rows := []map[string]any{}
			for _, j := range got.Jobs {
				rows = append(rows, map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "metadata": j.Metadata, "extras": j.Extras})
			}
			encoded, _ := json.Marshal(rows)
			var jobs []any
			_ = json.Unmarshal(encoded, &jobs)
			if got.Truncated != c.Truncated || got.VerifiedEmptyReason != c.VerifiedEmptyReason || !reflect.DeepEqual(jobs, c.Jobs) {
				t.Fatalf("Inline fields/truncation/expiry differ: jobs=%v expected=%v", jobs, c.Jobs)
			}
			if requests > 1 {
				t.Fatal("single page extraction repeated request")
			}
		})
	}
}
