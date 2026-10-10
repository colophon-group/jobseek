package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"testing"
)

func TestRealLocalizedHTTPSettlesCompleteFailedReserved(t *testing.T) {
	for _, provider := range []string{"talemetry", "prospective", "kipt"} {
		for _, mode := range []string{"complete", "failed", "reserved"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				board := "https://careers.example.com/search/jobs"
				metadata := `{"scraper_type":"json-ld"}`
				responses := map[string]string{}
				wantCount := 5
				if provider == "talemetry" {
					var cases []struct {
						Name      string
						Requests  []struct{ URL string }
						Responses []string
					}
					b, e := os.ReadFile("../api-sniffer-monitor/testdata/python_talemetry.json")
					if e != nil || json.Unmarshal(b, &cases) != nil {
						t.Fatal("original Talemetry fixture")
					}
					for _, c := range cases {
						if c.Name == "html-complete" {
							for i, r := range c.Requests {
								responses[r.URL] = c.Responses[i]
							}
						}
					}
				}
				if provider == "prospective" {
					var cases []struct {
						Name, Board string
						Metadata    map[string]any
						Requests    []struct{ URL, Method, Body string }
						Responses   []struct{ Body string }
					}
					b, e := os.ReadFile("../api-sniffer-monitor/testdata/python_prospective.json")
					if e != nil || json.Unmarshal(b, &cases) != nil {
						t.Fatal("original Prospective fixture")
					}
					for _, c := range cases {
						if c.Name == "localized" {
							board = c.Board
							c.Metadata["scraper_type"] = "skip"
							raw, _ := json.Marshal(c.Metadata)
							metadata = string(raw)
							for i, r := range c.Requests {
								responses[r.Method+" "+r.URL+" "+r.Body] = c.Responses[i].Body
							}
						}
					}
					wantCount = 1
				}
				if provider == "kipt" {
					board = "https://www.kipt.kharkov.ua/ua/vacancy.html"
					metadata = `{"scraper_type":"skip","max_age_days":30}`
					responses[board] = `<html><a href="/news/2000/vacancy_01_01_2000.pdf">Historical bulletin</a></html>`
					wantCount = 0
				}
				f := privateRichPipelineFixtureURL(t, provider, metadata, board)
				claim, circuits := claimFixture(t, f)
				client := &VerifiedDirectHTTP{client: verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if mode != "complete" {
						if mode == "reserved" {
							w.Header().Set("TDM-Reservation", "1")
						}
						w.WriteHeader(503)
						return
					}
					key := "https://" + r.Host + r.URL.String()
					if provider == "prospective" {
						body, err := io.ReadAll(r.Body)
						if err != nil {
							t.Error("request body read failed")
							w.WriteHeader(400)
							return
						}
						key = r.Method + " " + key + " " + string(body)
					}
					source, ok := responses[key]
					if !ok {
						t.Error("unexpected original fixture request")
						w.WriteHeader(400)
						return
					}
					fmt.Fprint(w, source)
				}))}
				result, e := RunGreenhouseClaim(context.Background(), f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("owned grouped settlement failed", e)
				}
				assertRichDeadlineAndLease(t, f, provider)
				var failures, missing, count int
				var reserved bool
				ctx := context.Background()
				if e := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); e != nil {
					t.Fatal(e)
				}
				if e := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
					t.Fatal(e)
				}
				if e := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&count); e != nil {
					t.Fatal(e)
				}
				wantFailure := 0
				if mode == "failed" {
					wantFailure = 1
				}
				if failures != wantFailure || reserved != (mode == "reserved") {
					t.Fatal("failure/publisher authority differs", failures, reserved)
				}
				if mode == "complete" {
					wantMissing := 1
					if wantCount == 0 {
						wantMissing = 0
					} // Original first zero observation retains blast-radius protection.
					if count != wantCount || missing != wantMissing {
						t.Fatal("complete discovery or absence differs", count, missing)
					}
				} else if count != 0 || missing != 0 {
					t.Fatal("unproved prefix or absence written")
				}
			})
		}
	}
}
