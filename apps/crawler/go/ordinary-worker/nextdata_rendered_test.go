package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type heldNextdataMonitor func(context.Context, queue.GreenhouseMonitorProfile, map[string]string, string) nextdataPage

func (f heldNextdataMonitor) FetchNextdataPage(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string, url string) nextdataPage {
	return f(ctx, p, c, url)
}
func (f heldNextdataMonitor) FetchMonitor(context.Context, queue.GreenhouseMonitorProfile, map[string]string, *http.Client) (RichDiscovery, error) {
	return RichDiscovery{}, queue.ErrUnsupportedProfile
}

func TestRealRenderedNextdataOwnedInventoryPolicyAndCanonicalSettlement(t *testing.T) {
	for _, mode := range []string{"rich", "urls", "rsc", "description-detail", "header", "meta", "malformed", "challenge", "changed-binding", "wrong-title"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"render":true,"path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"title","description":"body","locations":"city"},"scraper_type":"json-ld"}`
			if mode == "urls" || mode == "rsc" {
				metadata = `{"render":true,"path":"jobs","url_template":"https://example.com/jobs/{id}","scraper_type":"json-ld"}`
			}
			if mode == "rsc" {
				metadata = strings.TrimSuffix(metadata, "}") + `,"source":"rsc"}`
			}
			if mode == "description-detail" {
				metadata = strings.TrimSuffix(metadata, "}") + `,"scraper_config":{"enrich":["description"]}}`
			}
			if mode == "wrong-title" {
				metadata = strings.TrimSuffix(metadata, "}") + `,"expected_page_title":"Fixture tenant"}`
			}
			f := privateRichPipelineFixture(t, "nextdata", metadata, queue.Browser, queue.Simple)
			ctx := context.Background()
			if !f.a.RequiresRenderedDetails() {
				t.Fatal("rendered inventory hidden from startup")
			}
			if claim, err := f.a.Claim(ctx, queue.Simple); err != nil || claim != nil {
				t.Fatal("simple owner claimed browser monitor", err)
			}
			claim, err := f.a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal(err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			renderer := heldNextdataMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string, endpoint string) nextdataPage {
				if endpoint != p.Endpoint {
					t.Fatal("navigation left configured resource")
				}
				source := fmt.Sprintf(`<script id="__NEXT_DATA__">{"jobs":[{"id":"%s","title":"Senior Software Engineer","body":"<p>We are looking for a software engineer to build and maintain our platform.</p>","city":"Zurich"}]}</script>`, f.company)
				if mode == "rsc" {
					payload, _ := json.Marshal(`0:{"jobs":[{"id":"` + f.company + `"}]}` + "\n")
					source = `<script>self.__next_f.push([1,` + string(payload) + `])</script>`
				}
				if mode == "meta" {
					source = `<meta name="tdm-reservation" content="1">`
				}
				if mode == "challenge" {
					source = `<title>Just a moment...</title><div id="cf-chl-widget">Checking your browser</div>`
				}
				value := heldRenderedResult(source, p.Endpoint, 200)
				if mode == "header" {
					reserved := "1"
					value.GetSuccess().ResourcePolicy = &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reserved}
				}
				if mode == "malformed" {
					value.GetSuccess().Html.Complete = false
				}
				if mode == "changed-binding" {
					if _, err := f.pg.Exec(ctx, `UPDATE job_board SET metadata=metadata || '{"path":"other"}'::jsonb WHERE id=$1::uuid`, f.board); err != nil {
						t.Fatal(err)
					}
				}
				o, _, err := queue.RenderedNextdataMonitorOptions(c)
				if err != nil {
					t.Fatal(err)
				}
				return parseHeldNextdataPage(ctx, p, o, value)
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("rendered monitor used direct HTTP") })
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
			if mode == "changed-binding" {
				if err == nil || result.Settled {
					t.Fatal("changed rendered binding kept authority", err)
				}
				return
			}
			if err != nil || result == nil || !result.Settled {
				t.Fatal("rendered monitor did not settle", result, err)
			}
			var due time.Time
			if err := f.pg.QueryRow(ctx, "SELECT next_check_at FROM job_board WHERE id=$1::uuid", f.board).Scan(&due); err != nil {
				t.Fatal(err)
			}
			score, err := f.r.ZScore(ctx, "monitors_browser:nextdata", f.board).Result()
			if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:browser").Val() != 0 {
				t.Fatal("browser deadline or lease conservation changed", err)
			}
			var reserved bool
			var failures int
			if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures); err != nil {
				t.Fatal(err)
			}
			if mode == "header" || mode == "meta" {
				if !reserved || result.Batches.Inserted != 0 {
					t.Fatal("rendered policy wrote inventory")
				}
				return
			}
			if mode == "malformed" || mode == "challenge" || mode == "wrong-title" {
				if failures != 1 || result.Batches.Inserted != 0 {
					t.Fatal("invalid held proof supplied successful inventory", result, failures)
				}
				return
			}
			if failures != 0 || result.Batches.Inserted != 1 {
				t.Fatal("held embedded inventory lost canonical write", result, failures)
			}
			var id, title string
			if err := f.pg.QueryRow(ctx, "SELECT id::text,coalesce(titles[1],'') FROM job_posting WHERE source_url=$1", "https://example.com/jobs/"+f.company).Scan(&id, &title); err != nil {
				t.Fatal(err)
			}
			if mode == "rich" || mode == "description-detail" {
				if title != "Senior Software Engineer" {
					t.Fatal("held rich title lost")
				}
			}
			if mode == "urls" || mode == "rsc" || mode == "description-detail" {
				if f.r.ZScore(ctx, "ft_scrapes_simple:example.com", id).Err() != nil {
					t.Fatal("rendered discovery lost direct detail intent")
				}
			}
		})
	}
}
