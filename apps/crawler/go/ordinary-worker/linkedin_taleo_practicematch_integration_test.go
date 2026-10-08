package worker

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"strings"
	"sync/atomic"
	"testing"
)

func TestRealLinkedInTaleoPracticeMatchCanonicalEffectsAndConservation(t *testing.T) {
	var linkedin struct {
		Inventories []struct {
			Name      string
			Responses []*string
		}
	}
	var taleo struct {
		Inventories []struct {
			Name   string
			Bodies map[string]*string
		}
	}
	for name, target := range map[string]any{"linkedin": &linkedin, "taleo": &taleo} {
		raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_" + name + ".json")
		if err != nil || json.Unmarshal(raw, target) != nil {
			t.Fatal(err)
		}
	}
	liBodies, liKeywords := []*string{}, []*string{}
	for _, c := range linkedin.Inventories {
		if c.Name == "pagination" {
			liBodies = c.Responses
		}
		if c.Name == "keywords-union" {
			liKeywords = c.Responses
		}
	}
	tBodies := map[string]*string{}
	for _, c := range taleo.Inventories {
		if c.Name == "total-complete" {
			tBodies = c.Bodies
		}
	}
	if len(liBodies) != 2 || len(liKeywords) != 2 || len(tBodies) != 2 {
		t.Fatal("complete Python fixtures absent")
	}
	for _, provider := range []string{"linkedin", "taleo", "practicematch"} {
		modes := []string{"complete", "late-failed", "reserved503"}
		if provider != "taleo" {
			modes = append(modes, "partial")
		}
		for _, mode := range modes {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				source, metadata, wanted := "", "", 0
				switch provider {
				case "linkedin":
					source = "https://www.linkedin.com/company/acme/jobs/"
					metadata = `{"company_id":"42","company_slug":"acme","scraper_type":"linkedin","scraper_config":{"enrich":["description","employment_type","job_location_type"]}}`
					wanted = 11
					if mode == "partial" {
						metadata = strings.Replace(metadata, `"company_id":"42"`, `"company_id":"42","keywords":"ACME"`, 1)
						wanted = 2
					}
				case "taleo":
					source = "https://phe.tbe.taleo.net/phe01/ats/careers/v2/searchResults?org=ACME&cws=1"
					metadata = `{"host":"phe.tbe.taleo.net","partition":"phe01","org":"ACME","cws":1,"scraper_type":"json-ld","scraper_config":{}}`
					wanted = 17
				case "practicematch":
					source = "https://employer.practicematch.com/employer/fixture/"
					metadata = `{"proxy":true,"scraper_type":"json-ld","scraper_config":{}}`
					wanted = 3
					if mode == "partial" {
						metadata = strings.Replace(metadata, `"proxy":true`, `"proxy":true,"max_pages":1`, 1)
						wanted = 2
					}
				}
				f := privateRichPipelineFixtureURL(t, provider, metadata, source)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					child := provider == "linkedin" && r.URL.Query().Get("start") == "10" || provider == "taleo" && r.URL.Query().Get("rowFrom") == "10" || provider == "practicematch" && r.Method == "POST"
					if mode == "reserved503" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if mode == "late-failed" && child {
						w.Write([]byte("later inventory failed"))
						return
					}
					w.Header().Set("Content-Type", "text/html")
					switch provider {
					case "linkedin":
						index := 0
						if child {
							index = 1
						}
						if mode == "partial" {
							if r.URL.Query().Get("keywords") != "" {
								index = 1
							}
							w.Write([]byte(*liKeywords[index]))
						} else {
							w.Write([]byte(*liBodies[index]))
						}
					case "taleo":
						key := "0"
						if child {
							key = "10"
						}
						w.Write([]byte(*tBodies[key]))
					case "practicematch":
						link := func(id string) string {
							return `<a href="https://www.practicematch.com/physicians/job-details.cfm/` + id + `/role">Job</a>`
						}
						if r.Method == "GET" {
							w.Write([]byte(`<input id="facilityID" value="42"><input id="facilityLandingURL" value="A &amp; B">` + link("1")))
							return
						}
						r.ParseForm()
						rows := ""
						if r.Form.Get("professionID") == "1" && r.Form.Get("pageNum") == "2" {
							rows = link("2")
						}
						if r.Form.Get("professionID") == "-1" && r.Form.Get("pageNum") == "1" {
							rows = link("3")
						}
						w.Header().Set("Content-Type", "application/json")
						json.NewEncoder(w).Encode(map[string]string{"OPPLISTINGSHTML": rows})
					}
				}))
				if provider == "practicematch" {
					client = credentialedProxyFixture(t, client)
				}
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal(result, err)
				}
				assertRichDeadlineAndLease(t, f, provider)
				var failures int
				var reserved, active bool
				var title string
				if err = f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); err != nil {
					t.Fatal(err)
				}
				if err = f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); err != nil {
					t.Fatal(err)
				}
				if mode == "late-failed" || mode == "reserved503" {
					if !active || title != "Original" || result.Batches.Inserted != 0 || reserved != (mode == "reserved503") || mode == "late-failed" && failures != 1 {
						t.Fatal("failure changed canonical state", active, title, failures, reserved, result.Batches)
					}
					return
				}
				if result.Batches.Inserted != wanted || failures != 0 || reserved || mode == "partial" && !active {
					t.Fatal("inventory effects differ", result.Batches, wanted, failures, reserved, active)
				}
				var detailIntents, descriptions int
				if err = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid AND is_active AND next_scrape_at IS NOT NULL", f.board, f.original).Scan(&detailIntents); err != nil || detailIntents != wanted {
					t.Fatal("detail schedule lost", detailIntents, wanted, err)
				}
				if err = f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions d JOIN job_posting j ON j.id=d.posting_id WHERE j.board_id=$1::uuid AND j.id<>$2::uuid", f.board, f.original).Scan(&descriptions); err != nil || descriptions != 0 {
					t.Fatal("monitor fabricated descriptions", descriptions, err)
				}
				if provider == "linkedin" {
					var rich int
					if err = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid AND cardinality(titles)>0 AND cardinality(location_ids)>0", f.board, f.original).Scan(&rich); err != nil || rich != wanted {
						t.Fatal("rich summary lost", rich, wanted, err)
					}
				}
			})
		}
	}
}

func TestRealPracticeMatchProxyOwnerRejectsDirectClient(t *testing.T) {
	f := privateRichPipelineFixtureURL(t, "practicematch", `{"proxy":true,"scraper_type":"json-ld","scraper_config":{}}`, "https://employer.practicematch.com/employer/fixture/")
	claim, circuits := claimFixture(t, f)
	var requests atomic.Int32
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) { requests.Add(1) }))
	result, err := RunGreenhouseClaim(context.Background(), f.a, claim, client, richPipelinePreparer(t, f), circuits)
	if err == nil || result != nil && result.Settled || requests.Load() != 0 {
		t.Fatal("proxy owner used direct egress", result, err, requests.Load())
	}
}
