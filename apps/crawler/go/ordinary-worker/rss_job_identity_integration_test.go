package worker

import (
	"context"
	"fmt"
	"net/http"
	"sync/atomic"
	"testing"
)

func TestRealRSSJobInviteIdentityPreservesWholeInventoryAndLease(t *testing.T) {
	for _, mode := range []string{"resolved", "foreign", "nonredirect", "reserved", "late-invalid"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"preset":"successfactors","resolve_job_invite_identity":true,"scraper_type":"skip"}`
			f := privateRichPipelineFixture(t, "rss", metadata)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			var calls atomic.Int32
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/googlefeed.xml" {
					fmt.Fprint(w, `<rss xmlns:g="http://base.google.com/ns/1.0"><channel><item><link>https://example.com/job/Alias/1/</link><title>Software Engineer</title><description><![CDATA[<p>Python.</p>]]></description><g:location>Zurich, Switzerland</g:location></item><item><link>https://example.com/job/Alias/2/</link><title>Security Engineer</title><description><![CDATA[<p>Security.</p>]]></description><g:location>Zurich, Switzerland</g:location></item></channel></rss>`)
					return
				}
				calls.Add(1)
				if r.Method != http.MethodHead || r.Host != "example.com" {
					t.Error("identity request escaped HEAD/origin")
				}
				location := "/job/Engineer/123-de_DE/"
				if r.URL.Path == "/job/Alias/2/" {
					location = "/job/Engineer/456-en_US/"
				}
				switch mode {
				case "foreign":
					location = "https://foreign.example/job/Engineer/123-de_DE/"
				case "nonredirect":
					w.WriteHeader(200)
					return
				case "reserved":
					w.Header().Set("TDM-Reservation", "1")
				case "late-invalid":
					if r.URL.Path == "/job/Alias/2/" {
						location = "/unexpected/"
					}
				}
				w.Header().Set("Location", location)
				w.WriteHeader(302)
			})
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || !result.Settled {
				t.Fatal(result, err)
			}
			var count, missing, failures int
			var active, reserved bool
			if err = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count); err != nil {
				t.Fatal(err)
			}
			if err = f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
				t.Fatal(err)
			}
			if err = f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); err != nil {
				t.Fatal(err)
			}
			if mode == "resolved" {
				if count != 3 || result.Batches.Inserted != 2 || active || failures != 0 || calls.Load() != 2 {
					t.Fatal("resolved canonical inventory lost", result, count, failures, calls.Load())
				}
				var identities int
				if err = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND source_url IN ('https://example.com/job/Engineer/123-de_DE/','https://example.com/job/Engineer/456-en_US/')", f.board).Scan(&identities); err != nil || identities != 2 {
					t.Fatal("resolved source identities lost", identities, err)
				}
			} else {
				if count != 1 || !active || missing != 3 {
					t.Fatal("failed identity published prefix or absence", result, count, missing)
				}
				if mode == "reserved" {
					if !reserved || failures != 0 {
						t.Fatal("publisher policy lost", result)
					}
				} else if failures != 1 {
					t.Fatal("identity failure lost", result)
				}
			}
			assertRichDeadlineAndLease(t, f, "rss")
		})
	}
}
