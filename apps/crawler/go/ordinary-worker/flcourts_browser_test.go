package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"html"
	"net/http"
	"os"
	"reflect"
	"testing"

	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestFloridaCourtsHeldStreamMatchesActualPythonDiscovery(t *testing.T) {
	body, err := os.ReadFile("../api-sniffer-monitor/testdata/python_flcourts_browser.json")
	var corpus struct {
		Metadata json.RawMessage
		Cases    []struct {
			Name, HTML string
			Error      bool
			Chunks     []any
		}
	}
	if err != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) != 12 {
		t.Fatal("actual Python browser-source corpus missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			config := map[string]string{"crawler_type": "nextdata", "monitor_needs_browser": "1", "metadata": string(corpus.Metadata), "board_url": "https://example.com/careers"}
			p := queue.GreenhouseMonitorProfile{Provider: "nextdata", Profile: "nextdata.rendered-items/v1", Endpoint: config["board_url"]}
			o, _, err := queue.RenderedNextdataMonitorOptions(config)
			if err != nil {
				t.Fatal(err)
			}
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("browser-source monitor fell back to HTTP") })
			chunks := []any{}
			_, err = discoverNextdataWithPages(context.Background(), client.client, p, config, func(jobs []RichMonitorJob) error {
				rows := []map[string]any{}
				for _, j := range jobs {
					rows = append(rows, map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "metadata": j.Metadata, "language": j.Language, "extras": nil, "localizations": nil, "base_salary": nil, "source_identity": nullableNextdataIdentity(j.SourceIdentity)})
				}
				encoded, _ := json.Marshal(rows)
				var values any
				_ = json.Unmarshal(encoded, &values)
				chunks = append(chunks, values)
				return nil
			}, func(ctx context.Context, endpoint string) nextdataPage {
				return parseHeldNextdataPage(ctx, p, o, heldRenderedResult(c.HTML, endpoint, 200))
			})
			if (err != nil) != c.Error || !reflect.DeepEqual(chunks, c.Chunks) {
				t.Fatalf("browser-source stream differs: error=%v chunks=%v expected=%v", err, chunks, c.Chunks)
			}
		})
	}
}

func TestRealFloridaCourtsHeldInventoryWritesOrFailsAtomically(t *testing.T) {
	for _, mode := range []string{"valid", "missing", "late-invalid"} {
		t.Run(mode, func(t *testing.T) {
			md, _ := json.Marshal(map[string]any{"source": "browser", "browser_expression": apisniffer.FloridaCourtsBrowserExpression, "path": "items", "url_template": "{url}", "fields": map[string]string{"title": "title", "description": "body", "locations": "city"}, "scraper_type": "skip"})
			f := privateRichPipelineFixture(t, "nextdata", string(md), queue.Browser, queue.Simple)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal(err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			renderer := heldNextdataMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string, endpoint string) nextdataPage {
				items := []any{map[string]any{"location": map[string]string{"url": "/Services/Human-Resources/employment/Jobs/" + f.company}, "content": map[string]any{"fields": map[string]string{"title": "Senior Software Engineer", "body": "<p>We are looking for a software engineer to build and maintain our platform.</p>", "city": "Zurich"}}}}
				if mode == "late-invalid" {
					items = append(items, nil)
				}
				payload, _ := json.Marshal(map[string]any{"items": items})
				markup := `<div class="JobListings_ibexa" data-content="` + html.EscapeString(string(payload)) + `"></div>`
				root, _ := json.Marshal(map[string]any{"props": map[string]any{"pageProps": map[string]any{"pageData": map[string]any{"description": map[string]string{"html5": markup}}}}})
				source := `<script type="application/json" id='__NEXT_DATA__'>` + string(root) + `</script>`
				if mode == "missing" {
					source = "<p>No inventory</p>"
				}
				o, _, err := queue.RenderedNextdataMonitorOptions(c)
				if err != nil {
					t.Fatal(err)
				}
				return parseHeldNextdataPage(ctx, p, o, heldRenderedResult(source, endpoint, 200))
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("browser-source monitor used HTTP") })
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
			if err != nil || result == nil || !result.Settled {
				t.Fatal(result, err)
			}
			var failures, count int
			if err := f.pg.QueryRow(ctx, `SELECT consecutive_failures FROM job_board WHERE id=$1::uuid`, f.board).Scan(&failures); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, `SELECT count(*) FROM job_posting WHERE company_id=$1::uuid AND id<>$2::uuid`, f.company, f.original).Scan(&count); err != nil {
				t.Fatal(err)
			}
			var missing int
			if err := f.pg.QueryRow(ctx, `SELECT missing_count FROM job_posting WHERE id=$1::uuid`, f.original).Scan(&missing); err != nil {
				t.Fatal(err)
			}
			if mode != "valid" {
				if failures != 1 || count != 0 || result.Batches.Inserted != 0 || missing != 0 {
					t.Fatal("invalid browser source wrote a prefix or finalized successfully")
				}
				return
			}
			if failures != 0 || count != 1 || result.Batches.Inserted != 1 {
				t.Fatal("browser source failed canonical write", result)
			}
			var title string
			if err := f.pg.QueryRow(ctx, `SELECT titles[1] FROM job_posting WHERE company_id=$1::uuid AND source_url=$2`, f.company, fmt.Sprintf("https://www.flcourts.gov/Services/Human-Resources/employment/Jobs/%s", f.company)).Scan(&title); err != nil || title != "Senior Software Engineer" {
				t.Fatal("canonical URL/title lost", err)
			}
		})
	}
}
