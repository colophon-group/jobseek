package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type heldRenderedDetail func(context.Context, queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error)

func (f heldRenderedDetail) Fetch(ctx context.Context, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
	return f(ctx, p)
}

func heldRenderedResult(html, final string, status uint32) *runtimev1.BrowserResult {
	body := []byte(html)
	hash := sha256.Sum256(body)
	digest := hex.EncodeToString(hash[:])
	manifest := &runtimev1.ChunkManifest{Complete: true, TotalSizeBytes: uint64(len(body)), TotalSha256: digest}
	if len(body) > 0 {
		manifest.Chunks = []*runtimev1.DataChunk{{Sequence: 0, SizeBytes: uint64(len(body)), Sha256: digest, Storage: &runtimev1.DataChunk_InlineBody{InlineBody: body}}}
	}
	return &runtimev1.BrowserResult{ContractVersion: "crawler.runtime/v1", Backend: runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA, Outcome: &runtimev1.BrowserResult_Success{Success: &runtimev1.BrowserSuccess{FinalUrl: final, Status: &status, Html: manifest, ResourcePolicy: &runtimev1.ResourcePolicySignals{}}}}
}

func TestRealRenderedDetailPersistsHeldDocumentThroughBrowserAuthority(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld"} {
		t.Run(scraper, func(t *testing.T) {
			parser := `{"render":true,"defaults":{"language":"en"}}`
			html := nativeJSONLDHTML
			if scraper == "dom" {
				parser = `{"render":true,"steps":[{"tag":"h1","field":"title"},{"tag":"p","attr":"data-field=location","field":"location"},{"tag":"h2","text":"Role","offset":1,"field":"description","html":true}],"defaults":{"employment_type":"FULL_TIME","language":"en"}}`
				html = nativeDOMHTML
			}
			f, a, claim := independentDetailOwnedFixture(t, `{"scraper_type":"`+scraper+`","scraper_config":`+parser+`}`, "", queue.Browser)
			if !a.RequiresRenderedDetails() {
				t.Fatal("browser ownership hid required installed renderer")
			}
			calls := 0
			renderer := heldRenderedDetail(func(ctx context.Context, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
				calls++
				if p.SourceURL != "https://example.com/job/"+f.original || p.Profile != map[string]string{"dom": "dom.rendered-detail/v1", "json-ld": "jsonld.rendered-detail/v1"}[scraper] {
					t.Fatal("rendered canonical context differs")
				}
				return parseHeldDetail(ctx, p, heldRenderedResult(html, p.SourceURL, 200))
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("rendered detail issued direct HTTP") })
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(context.Background(), a, claim, client, richPipelinePreparer(t, f).Processor, circuits, renderer)
			if err != nil || !result.Settled || result.Cycle.Status != "succeeded" || calls != 1 || result.HTTP.Requests != 1 || result.HTTP.Responses != 1 {
				t.Fatal("rendered detail failed", result, err)
			}
			var title, descriptionHTML, employment, currency string
			var locations []int32
			var due time.Time
			var uploaded bool
			var canonicalHash *int64
			var pendingHash int64
			if err := f.pg.QueryRow(context.Background(), `SELECT p.titles[1],d.html,p.employment_type,p.salary_currency,p.location_ids,p.next_scrape_at,d.r2_uploaded,p.description_r2_hash,d.hash FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid`, f.original).Scan(&title, &descriptionHTML, &employment, &currency, &locations, &due, &uploaded, &canonicalHash, &pendingHash); err != nil {
				t.Fatal(err)
			}
			if title != "Senior Software Engineer" || !strings.Contains(descriptionHTML, "Salary CHF") || employment != "full_time" || currency != "CHF" || fmt.Sprint(locations) != "[2]" || uploaded || canonicalHash != nil || pendingHash == 0 {
				t.Fatal("rendered canonical fields/pending description differ")
			}
			score, err := f.r.ZScore(context.Background(), "scrapes_browser:example.com", f.original).Result()
			if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(context.Background(), "inflight:browser").Val() != 0 || f.r.ZCard(context.Background(), "scrapes_simple:example.com").Val() != 0 {
				t.Fatal("rendered browser deadline/lease differs", err)
			}
		})
	}
}

// This fixture uses the real held-document validation/parser; it has no HTTP
// transport. The pinned mutual-TLS wire client is verified in its own package.
func parseHeldDetail(ctx context.Context, p queue.WorkdayDetailProfile, result *runtimev1.BrowserResult) (map[string]any, *policy.Reservation, error) {
	scraper, options := "dom", p.DOMConfig
	if p.Profile == "jsonld.rendered-detail/v1" {
		scraper, options = "json-ld", p.JSONLDConfig
	}
	parser, _ := json.Marshal(options)
	return parseHeldRenderedResult(ctx, p.SourceURL, scraper, parser, result)
}

func TestRealRenderedDetailRetainsPolicyGoneAndFreshCanonicalState(t *testing.T) {
	for _, mode := range []string{"header-reserved", "meta-reserved", "existing-reserved", "fresh-reserved", "inactive-header-reserved", "404-gone", "503-transient", "changed-authority", "canceled"} {
		t.Run(mode, func(t *testing.T) {
			f, a, claim := independentDetailOwnedFixture(t, `{"scraper_type":"dom","scraper_config":{"render":true,"steps":[{"tag":"h1","field":"title"},{"tag":"h2","text":"Role","offset":1,"field":"description","html":true}]}}`, "", queue.Browser)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if mode == "existing-reserved" {
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET tdm_reserved=true WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
			}
			calls := 0
			renderer := heldRenderedDetail(func(ctx context.Context, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
				calls++
				held := heldRenderedResult(nativeDOMHTML, p.SourceURL, 200)
				success := held.GetSuccess()
				switch mode {
				case "header-reserved", "inactive-header-reserved":
					one, policyURL, status := "1", "https://example.com/policy", uint32(410)
					success.ResourcePolicy.TdmReservationHeader = &one
					success.ResourcePolicy.TdmPolicyHeader = &policyURL
					success.Status = &status
					if mode == "inactive-header-reserved" {
						if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET is_active=false WHERE id=$1::uuid", f.original); err != nil {
							t.Fatal(err)
						}
					}
				case "meta-reserved":
					held = heldRenderedResult(`<meta name="tdm-reservation" content="1">`, p.SourceURL, 410)
				case "fresh-reserved":
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET tdm_reserved=true WHERE id=$1::uuid", f.original); err != nil {
						t.Fatal(err)
					}
				case "404-gone":
					status := uint32(404)
					success.Status = &status
				case "503-transient":
					status := uint32(503)
					success.Status = &status
				case "changed-authority":
					if _, err := f.pg.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE state='active'"); err != nil {
						t.Fatal(err)
					}
				case "canceled":
					cancel()
					return nil, nil, ctx.Err()
				}
				return parseHeldDetail(ctx, p, held)
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("rendered failure fell back to HTTP") })
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits, renderer)
			if mode == "changed-authority" || mode == "canceled" {
				if err == nil || result.Settled {
					t.Fatal("lost authority/cancellation settled")
				}
			} else if err != nil || !result.Settled {
				t.Fatal("rendered terminal outcome unsettled", err)
			}
			var title string
			var active, reserved bool
			var descriptions int
			var due *time.Time
			if err := f.pg.QueryRow(context.Background(), `SELECT titles[1],is_active,tdm_reserved,next_scrape_at,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &active, &reserved, &due, &descriptions); err != nil {
				t.Fatal(err)
			}
			if title != "Original" || descriptions != 0 {
				t.Fatal("non-success rendered content written")
			}
			if active != (mode != "404-gone" && mode != "inactive-header-reserved") {
				t.Fatal("rendered visibility differs")
			}
			if strings.Contains(mode, "reserved") && !reserved {
				t.Fatal("publisher reservation lost")
			}
			if mode == "existing-reserved" && calls != 0 {
				t.Fatal("reserved posting rendered")
			}
			if mode == "inactive-header-reserved" && (due != nil || result.Cycle.Status != "unscheduled") {
				t.Fatal("inactive opt-out rescheduled")
			}
			if mode != "changed-authority" && mode != "canceled" && f.r.ZCard(context.Background(), "inflight:browser").Val() != 0 {
				t.Fatal("terminal render retained browser lease")
			}
		})
	}
}
