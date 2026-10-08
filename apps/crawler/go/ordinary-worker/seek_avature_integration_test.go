package worker

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"testing"
)

func TestRealSeekAvatureCanonicalURLOnlySettlementAndFailureConservation(t *testing.T) {
	for _, provider := range []string{"seek", "avature"} {
		raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_" + provider + ".json")
		var records []json.RawMessage
		if e != nil || json.Unmarshal(raw, &records) != nil {
			t.Fatal("reference", e)
		}
		var source string
		var pages []json.RawMessage
		var truncatedPages []json.RawMessage
		wanted := 0
		for _, r := range records {
			var c struct {
				Name, Kind, Source string
				Pages              []json.RawMessage
				Output             json.RawMessage
				Error              bool
			}
			json.Unmarshal(r, &c)
			if c.Name == "total-drift" {
				truncatedPages = c.Pages
			}
			if c.Kind != "inventory" || c.Error || len(pages) > 0 {
				continue
			}
			source, pages = c.Source, c.Pages
			if provider == "seek" {
				var urls []string
				json.Unmarshal(c.Output, &urls)
				wanted = len(urls)
			} else {
				var out struct{ URLs []string }
				json.Unmarshal(c.Output, &out)
				wanted = len(out.URLs)
			}
		}
		modes := []string{"complete", "late-failed", "reserved503"}
		if provider == "avature" {
			modes = append(modes, "truncated")
		}
		for _, mode := range modes {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				metadata := `{"scraper_type":"json-ld","scraper_config":{}}`
				if provider == "avature" {
					metadata = `{"listing_url":"https://acme.avature.net/careers/SearchJobs","portal_id":"4","scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}]}}`
				}
				f := privateRichPipelineFixtureURL(t, provider, metadata, source)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if mode == "reserved503" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					page := 0
					if provider == "seek" && r.URL.Query().Get("page") == "2" || provider == "avature" && r.URL.RawQuery != "" {
						page = 1
					}
					media := "application/json"
					if provider == "avature" {
						media = "text/html"
					}
					w.Header().Set("Content-Type", media)
					if mode == "late-failed" && page == 1 {
						w.Write([]byte("broken"))
						return
					}
					body := pages[page]
					if mode == "truncated" {
						body = truncatedPages[page]
					}
					if provider == "avature" {
						var text string
						json.Unmarshal(body, &text)
						body = []byte(text)
					}
					w.Write(body)
				}))
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal(result, e)
				}
				assertRichDeadlineAndLease(t, f, provider)
				var failures int
				var reserved, active bool
				var title string
				if e := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); e != nil {
					t.Fatal(e)
				}
				if e := f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); e != nil {
					t.Fatal(e)
				}
				if mode != "complete" && mode != "truncated" {
					if !active || title != "Original" || result.Batches.Inserted != 0 || reserved != (mode == "reserved503") || mode == "late-failed" && failures != 1 {
						t.Fatal("failure changed canonical data", active, title, failures, reserved, result.Batches)
					}
					return
				}
				if result.Batches.Inserted != wanted || failures != 0 || reserved {
					t.Fatal(result.Batches, wanted, failures, reserved)
				}
				if mode == "truncated" && !active {
					t.Fatal("truncated inventory delisted an existing posting")
				}
				if provider == "avature" {
					var listing, portal string
					if err := f.pg.QueryRow(ctx, "SELECT metadata->>'listing_url',metadata->>'portal_id' FROM job_board WHERE id=$1::uuid", f.board).Scan(&listing, &portal); err != nil || listing != source || portal != "4" {
						t.Fatal("bound listing metadata was not preserved", listing, portal, err)
					}
				}
				var detailIntents, descriptions int
				if e := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid AND is_active AND next_scrape_at IS NOT NULL", f.board, f.original).Scan(&detailIntents); e != nil || detailIntents != wanted {
					t.Fatal("scheduled detail intents missing", detailIntents, wanted, e)
				}
				if e := f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions d JOIN job_posting j ON j.id=d.posting_id WHERE j.board_id=$1::uuid AND j.id<>$2::uuid", f.board, f.original).Scan(&descriptions); e != nil || descriptions != 0 {
					t.Fatal("URL-only monitor fabricated descriptions", descriptions, e)
				}
			})
		}
	}
}
