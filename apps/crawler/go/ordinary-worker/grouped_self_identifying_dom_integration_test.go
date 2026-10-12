package worker

import (
	"context"
	"encoding/json"
	"html"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealGroupedSelfIdentifyingDOMCanonicalInventoryAndFailureAuthority(t *testing.T) {
	var cases []struct {
		Name, Slug string
		Board      string   `json:"board_url"`
		URLs       []string `json:"urls"`
	}
	raw, err := os.ReadFile("testdata/self_identifying_dom_original_urls.json")
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 8 {
		t.Fatal("complete original public URL reference unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			metadata, err := os.ReadFile("../ordinary-queue/testdata/self_identifying_" + c.Name + "_metadata.json")
			if err != nil {
				t.Fatal(err)
			}
			for _, mode := range []string{"complete", "publisher", "unproved_empty", "incomplete"} {
				t.Run(mode, func(t *testing.T) {
					f := privateRichPipelineFixtureURL(t, "dom", string(metadata), c.Board, queue.Browser, queue.Browser)
					ctx := context.Background()
					claim, err := f.a.Claim(ctx, queue.Browser)
					if err != nil || claim == nil {
						t.Fatal("original browser claim unavailable", err)
					}
					proxy, err := runtimeClaimUsesProxy(ctx, f.a, claim)
					if err != nil || proxy {
						t.Fatal("direct replacement selected proxy", err)
					}
					skip, err := runtimeClaimSkipsSSL(ctx, f.a, claim)
					if err != nil || skip {
						t.Fatal("verified TLS replacement selected legacy exception", err)
					}
					circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
					if err != nil {
						t.Fatal(err)
					}
					var source strings.Builder
					for _, u := range c.URLs {
						source.WriteString(`<a class="card-title" href="` + html.EscapeString(u) + `">Job</a>`)
					}
					document := source.String()
					documentFinal := c.Board
					if dir := os.Getenv("JOBSEEK_GROUPED_DOM_PHYSICAL_CAPTURE_DIR"); dir != "" {
						var physical struct {
							Status  string
							Cleanup bool `json:"child_cleanup_proved"`
							Result  struct {
								HTML   string
								Status int
								Final  string `json:"final_url"`
							}
						}
						body, err := os.ReadFile(filepath.Join(dir, c.Slug+"-lightpanda.json"))
						expectedFinal := c.Board
						if c.Name == "alten_spain" {
							expectedFinal += "?sortby=date&pagenum=1&per_page=100"
						}
						if err != nil || json.Unmarshal(body, &physical) != nil || physical.Status != "succeeded" || !physical.Cleanup || physical.Result.Status != 200 || physical.Result.Final != expectedFinal {
							t.Fatal("complete physical original document unavailable")
						}
						document = physical.Result.HTML
						documentFinal = physical.Result.Final
					}
					renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, config map[string]string) (RichDiscovery, error) {
						_, nav, err := queue.RenderedDOMMonitorOptions(config)
						if err != nil {
							t.Fatal(err)
						}
						for _, key := range []string{"proxy", "stealth", "skip_ssl", "transport_attempts"} {
							if _, ok := nav[key]; ok {
								t.Fatal("unsupported control reached direct navigation", key)
							}
						}
						if mode == "unproved_empty" {
							document = "<html><body>Temporarily unavailable</body></html>"
						}
						value := heldRenderedResult(document, documentFinal, 200)
						if mode == "publisher" {
							one := "1"
							value.GetSuccess().ResourcePolicy.TdmReservationHeader = &one
						}
						if mode == "incomplete" {
							value.GetSuccess().Html.Complete = false
						}
						return parseHeldRenderedMonitor(ctx, p, config, value)
					})
					client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Fatal("rendered inventory issued direct worker HTTP") })
					result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits, renderer)
					if err != nil || result == nil || !result.Settled {
						t.Fatal("group did not settle", err)
					}
					var count, missing, failures int
					var active, reserved bool
					if err := f.pg.QueryRow(ctx, "SELECT (SELECT count(*) FROM job_posting WHERE board_id=$1::uuid),consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&count, &failures, &reserved); err != nil {
						t.Fatal(err)
					}
					if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
						t.Fatal(err)
					}
					if mode == "complete" {
						if count != len(c.URLs)+1 || result.Batches.Inserted != len(c.URLs) || failures != 0 || missing != 1 || reserved {
							t.Fatal("original complete inventory changed", count, result.Batches.Inserted, missing, failures)
						}
						root, _ := url.Parse(c.Board)
						if f.r.ZCard(ctx, "ft_scrapes_browser:"+root.Hostname()).Val() != int64(len(c.URLs)) {
							t.Fatal("original browser detail routing lost")
						}
						expected := map[string]bool{}
						for _, u := range c.URLs {
							expected[u] = true
						}
						rows, err := f.pg.Query(ctx, "SELECT source_url FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original)
						if err != nil {
							t.Fatal(err)
						}
						defer rows.Close()
						for rows.Next() {
							var u string
							if rows.Scan(&u) != nil || !expected[u] {
								t.Fatal("canonical URL differs")
							}
							delete(expected, u)
						}
						if rows.Err() != nil || len(expected) != 0 {
							t.Fatal("canonical URLs incomplete")
						}
					} else {
						if count != 1 || result.Batches.Inserted != 0 || !active || missing != 0 {
							t.Fatal("uncertain/reserved source acquired absence authority")
						}
						if mode == "publisher" && (!reserved || failures != 0) || mode != "publisher" && (reserved || failures != 1) {
							t.Fatal("failure/reservation state changed")
						}
					}
					assertRichDeadlineAndLease(t, f, "dom", queue.Browser)
				})
			}
		})
	}
}
