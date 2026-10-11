package worker

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestLiveAPIPublicCanonicalReplay(t *testing.T) {
	originalDir, nativeDir := os.Getenv("JOBSEEK_API_LIVE_PUBLIC_CAPTURE_DIR"), os.Getenv("JOBSEEK_API_LIVE_NATIVE_CAPTURE_DIR")
	if originalDir == "" || nativeDir == "" {
		t.Skip("requires original/physical public inventory captures")
	}
	for _, slug := range []string{"hitachi-energy-careers", "innosuisse-main"} {
		t.Run(slug, func(t *testing.T) {
			raw, err := os.ReadFile(filepath.Join(originalDir, slug+"-original.json"))
			var original struct {
				Status    string
				Truncated bool
				Config    struct {
					BoardURL string `json:"board_url"`
					Metadata json.RawMessage
				}
				Jobs []api.Job
			}
			if err != nil || json.Unmarshal(raw, &original) != nil || original.Status != "succeeded" || original.Truncated {
				t.Fatal("complete original inventory unavailable")
			}
			native, err := os.ReadFile(filepath.Join(nativeDir, slug+"-native-inventory.json"))
			var inventory api.Inventory
			if err != nil || json.Unmarshal(native, &inventory) != nil || inventory.Truncated || len(inventory.Jobs) != len(original.Jobs) {
				t.Fatal("complete physical inventory unavailable")
			}
			f := privateRichPipelineFixtureURL(t, "api_sniffer", string(original.Config.Metadata), original.Config.BoardURL, queue.Browser)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal("claim", err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			renderer := heldMonitor(func(_ context.Context, p queue.GreenhouseMonitorProfile, _ map[string]string) (RichDiscovery, error) {
				return parseAPIReplayResponse(p, replay.Response{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: p.EffectiveConfigSHA256, Outcome: "success", Inventory: native})
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Fatal("rendered inventory used worker HTTP") })
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("public canonical cycle did not settle", err)
			}
			assertRichDeadlineAndLease(t, f, "api_sniffer", queue.Browser)
			rules, err := queue.FeedMonitorURLRules(map[string]string{"metadata": string(original.Config.Metadata)})
			if err != nil {
				t.Fatal(err)
			}
			expected := map[string]string{}
			for _, job := range original.Jobs {
				title, err := executor.CoerceText(job.Title)
				if err != nil || title == nil || *title == "" {
					t.Fatal("original title invalid")
				}
				expected[rules.Rewrite(job.URL)] = *title
			}
			rows, err := f.pg.Query(ctx, "SELECT source_url,titles[1] FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original)
			if err != nil {
				t.Fatal(err)
			}
			defer rows.Close()
			count := 0
			for rows.Next() {
				var source, title string
				if rows.Scan(&source, &title) != nil || expected[source] != title {
					t.Fatal("canonical source/title differs")
				}
				delete(expected, source)
				count++
			}
			if rows.Err() != nil || count != len(original.Jobs) || len(expected) != 0 || result.Batches.Inserted != len(original.Jobs) {
				t.Fatal("canonical inventory loss", count, result.Batches.Inserted)
			}
			t.Logf("physical_public_jobs=%d canonical_source_title_parity=%d exact_deadline_lease_settlement=true", count, count)
		})
	}
}
