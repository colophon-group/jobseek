package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"

	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealOracleADFCompleteSnapshotAndFailureConservation(t *testing.T) {
	for _, mode := range []string{"complete", "post-failure", "partial-list", "late-reserved-fragment", "detail-reserved", "unmatched-detail"} {
		t.Run(mode, func(t *testing.T) {
			md := strings.Replace(adfTestConfig()["metadata"], `"scraper_type":"skip"`, `"scraper_type":"json-ld"`, 1)
			f := privateRichPipelineFixtureURL(t, "dom", md, "https://example.com/jobs", queue.Browser)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal("ADF browser assignment unavailable")
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method == "POST" {
					if r.ParseForm() != nil {
						t.Error("invalid ADF form")
						w.WriteHeader(400)
						return
					}
					if r.PostForm.Get("oracle.adf.view.rich.RENDER") == "pt1:r1" {
						fmt.Fprint(w, adfTestPartial("pt1:r1", `<a href="/jobs?jobId=100">Permalink</a>`, "3"))
						return
					}
					if mode == "post-failure" {
						w.WriteHeader(503)
						return
					}
					fragment := adfTestRows(1, 1, false)
					if mode == "partial-list" {
						fragment = "<div>missing rows</div>"
					}
					if mode == "late-reserved-fragment" {
						fragment += `<meta name="tdm-reservation" content="1">`
					}
					fmt.Fprint(w, adfTestPartial("pt1:r1:0:lv1", fragment, "2"))
					return
				}
				if id := r.URL.Query().Get("jobId"); id != "" {
					if id != "100" && id != "99" {
						w.WriteHeader(404)
						return
					}
					index := 0
					if id == "99" {
						index = 1
					}
					if mode == "detail-reserved" && index == 1 {
						w.Header().Set("TDM-Reservation", "1")
					}
					if mode == "unmatched-detail" && index == 1 {
						index = 2
					}
					fmt.Fprintf(w, `<span id="d:ot3">%s posted</span><span id="d:ot1"><label>Engineer %d</label></span><span id="d:ot5">Paris</span>`, id, index)
					return
				}
				fmt.Fprint(w, `<form id="f1"><input type="hidden" name="org.apache.myfaces.trinidad.faces.FORM" value="f1"><input type="hidden" name="Adf-Window-Id" value="fresh"><input type="hidden" name="javax.faces.ViewState" value="1"></form><script>AdfPage.PAGE.setPageId('1');</script>`+adfTestRows(0, 1, true))
			}))
			renderer := &NativeRenderedDetails{client: &lp.Client{}, slots: make(chan struct{}, 1)}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("ADF claim did not settle", err)
			}
			assertRichDeadlineAndLease(t, f, "dom", queue.Browser)
			var inserted, missing, failures, scheduled int
			var reserved bool
			if err := f.pg.QueryRow(ctx, `SELECT p.missing_count,b.consecutive_failures,b.tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=b.id AND id<>p.id),(SELECT count(*) FROM job_posting WHERE board_id=b.id AND id<>p.id AND next_scrape_at IS NOT NULL) FROM job_posting p JOIN job_board b ON b.id=p.board_id WHERE p.id=$1::uuid`, f.original).Scan(&missing, &failures, &reserved, &inserted, &scheduled); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				if inserted != 2 || scheduled != 2 || missing != 1 || failures != 0 || reserved || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 2 {
					t.Fatal("complete ADF URL inventory/write/detail queue contract differs")
				}
			} else {
				wantFailures := 1
				if strings.Contains(mode, "reserved") {
					wantFailures = 0
				}
				if inserted != 0 || scheduled != 0 || missing != 0 || failures != wantFailures || reserved != strings.Contains(mode, "reserved") {
					t.Fatal("partial ADF snapshot changed canonical membership or policy")
				}
			}
		})
	}
}
