package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealDOMAdvertisedRangesConserveCompleteInventory(t *testing.T) {
	for _, mode := range []string{"complete", "repeat", "early-end", "overflow", "gap", "first-count", "max-pages", "tail-policy", "proved-empty"} {
		t.Run(mode, func(t *testing.T) {
			pagination := map[string]any{"param_name": "p_start", "start": 1, "increment": 1, "max_pages": 4, "transport_attempts": 1, "advertised_ranges": map[string]any{"selector": "li.range", "pattern": `^(\d+)-(\d+)$`}}
			if mode == "max-pages" {
				pagination["max_pages"] = 2
			}
			md, _ := json.Marshal(map[string]any{"scraper_type": "json-ld", "link_selector": "a.job", "pagination": pagination, "empty_states": []any{map[string]any{"selector": ".empty", "exact_text": "No jobs", "forbidden_link_selector": "a.job"}}})
			f := privateRichPipelineFixture(t, "dom", string(md))
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.URL.Path != "/careers" || calls > 1 && r.URL.Query().Get("p_start") != fmt.Sprint(calls) {
					t.Error("configured page identity changed")
				}
				if calls == 1 {
					if mode == "proved-empty" {
						fmt.Fprint(w, `<div class="empty">No jobs</div>`)
						return
					}
					tail := 3
					if mode == "gap" {
						tail = 4
					}
					fmt.Fprintf(w, `<li class="range">1-1</li><li class="range">2-2</li><li class="range">%d-%d</li>`, tail, tail)
					if mode == "first-count" {
						fmt.Fprint(w, `<a class="job" href="/jobs/extra">Unexpected</a>`)
					}
				} else if calls == 2 {
					switch mode {
					case "early-end":
						w.WriteHeader(404)
						return
					case "tail-policy":
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(403)
						return
					case "overflow":
						fmt.Fprint(w, `<a class="job" href="/jobs/extra1">Extra</a><a class="job" href="/jobs/extra2">Extra</a>`)
					}
				}
				identity := calls
				if mode == "repeat" {
					identity = 1
				}
				fmt.Fprintf(w, `<a class="job" href="/jobs/%s-%d">Engineer</a>`, f.company, identity)
			}))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("advertised range claim did not settle", err)
			}
			var inserted, missing, failures int
			var reserved bool
			if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid),(SELECT missing_count FROM job_posting WHERE id=$2::uuid) FROM job_board WHERE id=$1::uuid", f.board, f.original).Scan(&failures, &reserved, &inserted, &missing); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" || mode == "proved-empty" {
				wantInserted, wantCalls, wantMissing := 3, 3, 1
				if mode == "proved-empty" {
					wantInserted, wantCalls, wantMissing = 0, 1, 0 // Preserve the ordinary empty-cycle delist guard.
				}
				if inserted != wantInserted || missing != wantMissing || failures != 0 || reserved || calls != wantCalls {
					t.Fatal("complete range or empty proof changed", inserted, missing, failures, calls)
				}
			} else if inserted != 0 || missing != 0 || reserved != (mode == "tail-policy") || failures != map[bool]int{true: 0, false: 1}[mode == "tail-policy"] {
				t.Fatal("incomplete or reserved prefix changed canonical jobs", inserted, missing, failures, reserved)
			}
			assertRichDeadlineAndLease(t, f, "dom")
		})
	}
}

func TestDOMAdvertisedRangesWithoutExplicitZeroProofStillVisitsTail(t *testing.T) {
	c, err := dom.ListingOptions(dom.Object{"link_selector": "a.job", "pagination": map[string]any{"param_name": "page", "max_pages": 3, "advertised_ranges": map[string]any{"selector": "li", "pattern": `^(\d+)-(\d+)$`}}}, "https://example.com/careers")
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	ctx := context.Background()
	result, err := collectDOMListingPages(ctx, queue.GreenhouseMonitorProfile{Endpoint: c.BoardURL}, map[string]string{"metadata": `{}`}, c, func(p queue.GreenhouseMonitorProfile, options dom.ListingConfig, later bool) (RichDiscovery, error) {
		calls++
		body := "<p>Listing</p>"
		if calls == 2 {
			body = `<a class="job" href="/jobs/tail">Engineer</a>`
		}
		return parseDOMInventory(ctx, RichDiscovery{}, p, options, body, "", false, later)
	})
	if err != nil || calls != 3 || len(result.Jobs) != 1 || result.Jobs[0].URL != "https://example.com/jobs/tail" {
		t.Fatal("unproved empty root suppressed tail discovery", err, calls)
	}
}
