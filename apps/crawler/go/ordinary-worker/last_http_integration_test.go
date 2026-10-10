package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"testing"
)

func TestRealPeopleSoftGoneResourcesAndPublisherPrecedence(t *testing.T) {
	var cases []struct {
		Name, Board string
		Exchanges   []lastHTTPExchange
	}
	b, err := os.ReadFile("../api-sniffer-monitor/testdata/python_last_peoplesoft.json")
	if err != nil || json.Unmarshal(b, &cases) != nil {
		t.Fatal("original cumulative fixture unavailable")
	}
	var original struct {
		Name, Board string
		Exchanges   []lastHTTPExchange
	}
	for _, c := range cases {
		if c.Name == "complete-cumulative" {
			original = c
		}
	}
	if len(original.Exchanges) != 3 {
		t.Fatal("original cumulative fixture incomplete")
	}
	for step := 0; step < 3; step++ {
		for _, status := range []int{404, 410} {
			for _, reserved := range []bool{false, true} {
				t.Run(fmt.Sprintf("step%d/status%d/reserved%t", step, status, reserved), func(t *testing.T) {
					f := privateRichPipelineFixtureURL(t, "peoplesoft", `{"scraper_type":"peoplesoft","scraper_config":{"enrich":["description"]}}`, original.Board)
					ctx := context.Background()
					claim, circuits := claimFixture(t, f)
					used := 0
					client := &VerifiedDirectHTTP{client: verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						index := used
						used++
						if index == step {
							if reserved {
								w.Header().Set("TDM-Reservation", "1")
							}
							w.WriteHeader(status)
							return
						}
						if index > step || index >= len(original.Exchanges) {
							t.Error("request after terminal resource")
							w.WriteHeader(400)
							return
						}
						x := original.Exchanges[index]
						for k, v := range x.ResponseHeaders {
							if !strings.EqualFold(k, "content-length") {
								w.Header().Set(k, v)
							}
						}
						fmt.Fprint(w, x.Body)
					}))}
					result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
					if err != nil || result == nil || !result.Settled {
						t.Fatal("gone resource did not settle", err)
					}
					assertRichDeadlineAndLease(t, f, "peoplesoft")
					var failures, confirmations, missing, count int
					var actualReserved bool
					if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,gone_confirmation_count,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &confirmations, &actualReserved); err != nil {
						t.Fatal(err)
					}
					if err := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); err != nil {
						t.Fatal(err)
					}
					if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&count); err != nil {
						t.Fatal(err)
					}
					wantConfirmations := 1
					if reserved {
						wantConfirmations = 0
						if result.Cycle.Status != "publisher_reserved" {
							t.Fatal("publisher signal lost to gone status")
						}
					}
					if failures != 0 || confirmations != wantConfirmations || actualReserved != reserved || missing != 0 || count != 0 || used != step+1 {
						t.Fatal("original gone confirmation, no-prefix or publisher contract changed", failures, confirmations, actualReserved, missing, count, used)
					}
				})
			}
		}
	}
}

func TestRealLastHTTPFourProviderSettlement(t *testing.T) {
	for _, provider := range []string{"infor", "peoplesoft", "papa_johns", "unisante"} {
		for _, mode := range []string{"complete", "failed", "reserved"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				board, metadata := "", `{"scraper_type":"skip"}`
				exchanges := []lastHTTPExchange{}
				wantURLs := []string{}
				wantIdentity := ""
				if provider == "infor" || provider == "peoplesoft" {
					var cases []struct {
						Name, Board string
						Exchanges   []lastHTTPExchange
						Jobs        []map[string]any
					}
					b, err := os.ReadFile("../api-sniffer-monitor/testdata/python_last_" + provider + ".json")
					if err != nil || json.Unmarshal(b, &cases) != nil {
						t.Fatal("original monitor fixture unavailable")
					}
					c := cases[0]
					board = c.Board
					exchanges = c.Exchanges
					for _, job := range c.Jobs {
						wantURLs = append(wantURLs, job["url"].(string))
					}
					metadata = `{"scraper_type":"` + provider + `","scraper_config":{"enrich":["description"]}}`
				} else if provider == "papa_johns" {
					board = "https://jobs.papajohns.com/jobs/"
					metadata = `{"proxy":true,"scraper_type":"json-ld","scraper_config":{"proxy":true}}`
					var cases []struct {
						Source string
						URLs   []string
					}
					b, err := os.ReadFile("../api-sniffer-monitor/testdata/python_last_papa.json")
					if err != nil || json.Unmarshal(b, &cases) != nil {
						t.Fatal("original Papa fixture unavailable")
					}
					exchanges = []lastHTTPExchange{{Method: "GET", URL: board, Body: cases[0].Source}}
					wantURLs = cases[0].URLs
				} else {
					board = "https://emploi.unisante.ch/index.php/offres"
					metadata = `{"scraper_type":"skip","identity_migration":"unisante-provider-reference-v1"}`
					var corpus struct {
						Listings []struct{ Name, Source string }
						Details  []struct {
							Name, Source string
							Job          map[string]any
						}
					}
					b, err := os.ReadFile("../api-sniffer-monitor/testdata/python_last_unisante.json")
					if err != nil || json.Unmarshal(b, &corpus) != nil {
						t.Fatal("original Unisante fixture unavailable")
					}
					listing, detail := "", ""
					for _, c := range corpus.Listings {
						if c.Name == "normal" {
							listing = c.Source
						}
					}
					for _, c := range corpus.Details {
						if c.Name == "evergreen" {
							detail = c.Source
							wantURLs = []string{c.Job["url"].(string)}
							wantIdentity = c.Job["source_identity"].(string)
							listing = strings.ReplaceAll(listing, "1405-role", "medecin-role")
						}
					}
					if listing == "" || detail == "" {
						t.Fatal("original complete Unisante fixture missing")
					}
					exchanges = []lastHTTPExchange{{Method: "GET", URL: board, Body: listing}, {Method: "GET", URL: "https://emploi.unisante.ch/offres", Body: listing}, {Method: "GET", URL: wantURLs[0], Body: detail}}
				}
				f := privateRichPipelineFixtureURL(t, provider, metadata, board)
				ctx := context.Background()
				if provider == "unisante" {
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,source_identity=$2 WHERE id=$1::uuid", f.original, wantURLs[0]); err != nil {
						t.Fatal(err)
					}
				}
				claim, circuits := claimFixture(t, f)
				used := 0
				client := &VerifiedDirectHTTP{client: verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if mode != "complete" {
						if mode == "reserved" {
							w.Header().Set("TDM-Reservation", "1")
						}
						w.WriteHeader(503)
						return
					}
					if used >= len(exchanges) {
						t.Error("extra provider request")
						w.WriteHeader(400)
						return
					}
					x := exchanges[used]
					used++
					for k, v := range x.ResponseHeaders {
						if !strings.EqualFold(k, "content-length") {
							w.Header().Set(k, v)
						}
					}
					for _, v := range x.SetCookies {
						w.Header().Add("Set-Cookie", v)
					}
					fmt.Fprint(w, x.Body)
				}))}
				if provider == "papa_johns" {
					client = credentialedProxyFixture(t, client)
				}
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("complete provider lifecycle failed to settle", err)
				}
				assertRichDeadlineAndLease(t, f, provider)
				var failures, missing, count int
				var reserved bool
				var receipt json.RawMessage
				if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved,metadata->'_identity_migration_receipt' FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved, &receipt); err != nil {
					t.Fatal(err)
				}
				if err := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); err != nil {
					t.Fatal(err)
				}
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&count); err != nil {
					t.Fatal(err)
				}
				wantFailure := 0
				if mode == "failed" {
					wantFailure = 1
				}
				if failures != wantFailure || reserved != (mode == "reserved") {
					var lastError *string
					if err := f.pg.QueryRow(ctx, "SELECT last_error FROM job_board WHERE id=$1::uuid", f.board).Scan(&lastError); err != nil {
						t.Fatal(err)
					}
					if lastError != nil {
						t.Error("bounded native failure classification", *lastError)
					}
					t.Fatal("terminal failure/publisher policy changed", failures, reserved)
				}
				if mode != "complete" {
					if count != 0 || missing != 0 || len(receipt) > 0 {
						t.Fatal("unproved inventory wrote prefix, absence or adoption")
					}
					return
				}
				if used != len(exchanges) {
					t.Fatal("original complete request sequence changed")
				}
				if provider == "unisante" {
					var id string
					if err := f.pg.QueryRow(ctx, "SELECT source_identity FROM job_posting WHERE id=$1::uuid", f.original).Scan(&id); err != nil || id != wantIdentity || len(receipt) == 0 || count != 0 || missing != 0 {
						t.Fatal("full fenced adoption lost posting identity", err)
					}
				} else if count != len(wantURLs) || missing != 1 {
					t.Fatal("full inventory or absence changed", count, missing)
				}
				for _, source := range wantURLs {
					var title string
					var scheduled bool
					var bodies int
					if err := f.pg.QueryRow(ctx, `SELECT coalesce(titles[1],''),next_scrape_at IS NOT NULL,(SELECT count(*) FROM descriptions d WHERE d.posting_id=p.id) FROM job_posting p WHERE board_id=$1::uuid AND source_url=$2`, f.board, source).Scan(&title, &scheduled, &bodies); err != nil {
						t.Fatal(err)
					}
					// Original adoption preserves the selected legacy row's existing
					// scrape due; it cancels only the aliases which it retires.
					if provider == "papa_johns" && title != "" || provider != "papa_johns" && title == "" || !scheduled || bodies != map[bool]int{true: 1, false: 0}[provider == "unisante"] {
						t.Fatal("canonical title, description or delegated detail intent changed", title == "", scheduled, bodies)
					}
				}
			})
		}
	}
}
