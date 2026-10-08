package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
)

func TestRealJarviAndJob51CanonicalFieldsAndTerminalSettlement(t *testing.T) {
	for _, c := range jarviJob51InventoryCases(t) {
		if c.Mode != "rich" {
			continue
		}
		for _, mode := range []string{"complete", "partial", "reserved", "child-reserved", "gone", "wrong-employer"} {
			if (mode == "wrong-employer" || mode == "child-reserved") && c.Provider != "job51" {
				continue
			}
			t.Run(c.Provider+"/"+mode, func(t *testing.T) {
				var md map[string]any
				if json.Unmarshal(c.Board.Metadata, &md) != nil {
					t.Fatal("fixture metadata unavailable")
				}
				md["scraper_type"] = "skip"
				metadata, _ := json.Marshal(md)
				f := privateRichPipelineFixtureURL(t, c.Provider, string(metadata), c.Board.URL)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				exchanges := map[string]string{}
				for _, x := range c.Exchanges {
					exchanges[x.URL] = x.Body
				}
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if mode == "reserved" || mode == "child-reserved" && r.URL.Path == "/job_detail.php" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if mode == "gone" {
						w.WriteHeader(404)
						return
					}
					resource := "https://" + r.Host + r.URL.RequestURI()
					body, ok := exchanges[resource]
					if !ok {
						t.Error("unknown public resource")
						w.WriteHeader(400)
						return
					}
					if mode == "partial" && c.Provider == "job51" && r.URL.Path == "/job_detail.php" {
						fmt.Fprint(w, "jsoncallback({})")
						return
					}
					if mode == "wrong-employer" && r.URL.Path == "/job_detail.php" {
						raw := body[len("jsoncallback(") : len(body)-1]
						var payload map[string]any
						if json.Unmarshal([]byte(raw), &payload) != nil {
							t.Error("fixture JSONP unavailable")
							return
						}
						payload["resultbody"].(map[string]any)["ctmid"] = "12346"
						encoded, _ := json.Marshal(payload)
						body = "jsoncallback(" + string(encoded) + ")"
					}
					if mode == "partial" && c.Provider == "jarvi" {
						var payload map[string]any
						if json.Unmarshal([]byte(body), &payload) != nil {
							t.Error("fixture inventory unavailable")
							return
						}
						payload["total"] = 2
						encoded, _ := json.Marshal(payload)
						body = string(encoded)
					}
					w.Header().Set("Content-Type", "application/json; charset=utf-8")
					fmt.Fprint(w, body)
				}))
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("real owned claim did not settle", e, result)
				}
				assertRichDeadlineAndLease(t, f, c.Provider)
				var reserved bool
				var failures, gone int
				if e = f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &gone); e != nil {
					t.Fatal(e)
				}
				wantFailure, wantGone := 0, 0
				if mode == "partial" && c.Provider == "job51" || mode == "wrong-employer" || mode == "gone" && c.Provider == "jarvi" {
					wantFailure = 1
				}
				if mode == "gone" && c.Provider == "job51" {
					wantGone = 1
				}
				if reserved != (mode == "reserved" || mode == "child-reserved") || failures != wantFailure || gone != wantGone {
					t.Fatal("terminal board state differs", reserved, failures, gone)
				}
				writes := mode == "complete" || mode == "partial" && c.Provider == "jarvi"
				if !writes {
					if result.Batches.Inserted != 0 {
						t.Fatal("failed inventory published a prefix", result.Batches)
					}
					var active bool
					var title string
					if e = f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); e != nil || !active || title != "Original" {
						t.Fatal("failed inventory mutated original posting", e)
					}
					return
				}
				if result.Batches.Inserted != len(c.Jobs) {
					t.Fatal("complete inventory lost postings", result.Batches, len(c.Jobs))
				}
				if mode == "partial" {
					var active bool
					if e = f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active); e != nil || !active {
						t.Fatal("truncated Jarvi inventory delisted unseen posting", e)
					}
				}
				for _, expected := range c.Jobs {
					job, e := secondaryRichJob(expected)
					if e != nil || job.Description == nil {
						t.Fatal("canonical description missing", e)
					}
					identity := expected["url"].(string)
					expectedHTML := *job.Description
					if c.Provider == "job51" {
						identity = expected["source_identity"].(string)
						// Existing processor extras append the original fixture's
						// skills; its responsibilities/qualifications already occur
						// in the main description and are not repeated.
						expectedHTML += "\n<h3>Skills</h3>\n<ul><li>Python</li><li>Go</li></ul>"
					}
					var count int
					if e = f.pg.QueryRow(ctx, `SELECT count(*) FROM descriptions d JOIN job_posting p ON p.id=d.posting_id WHERE p.board_id=$1::uuid AND p.source_url=$2 AND d.html=$3 AND NOT d.r2_uploaded AND p.next_scrape_at IS NULL AND coalesce(p.source_identity,'')=$4 AND p.titles[1]=$5`, f.board, expected["url"], expectedHTML, identity, *job.Title).Scan(&count); e != nil || count != 1 {
						t.Fatal("canonical HTML, identity, title, R2 intent or skip schedule changed", e, count)
					}
				}
			})
		}
	}
}
