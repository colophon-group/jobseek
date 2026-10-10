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

const candidatusFixtureListing = "https://carrieres.candidatus.com/site-emploi,ZmFrZQ"

func candidatusFixtureHTML() string {
	return `<form name="RECRUTEMENT_LISTEANNONCES_XMOD10" method="post" action="?id=ZmFrZQ&amp;lang=&amp;intra=&amp;v=&amp;rub="><input name="WD_JSON_PROPRIETE_"><input name="WD_BUTTON_CLICK_"><input name="WD_ACTION_"><input name="A18"><input name="A18_DEB" value="1"><input name="_A18_OCC" value="2"></form><a id="c-1-A20" href="javascript:_PAGE_.A18.value=1;_JSL(_PAGE_,'A20')">Engineer</a><a id="c-2-A20" href="javascript:_PAGE_.A18.value=2;_JSL(_PAGE_,'A20')">Scientist</a>`
}

func TestRealCandidatusCompletePostbacksPolicyAndAtomicSettlement(t *testing.T) {
	for _, mode := range []string{"complete", "root-reserved", "post-reserved", "later-failure", "no-redirect", "changed-listing", "duplicate-detail", "foreign-action", "foreign-redirect"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixtureURL(t, "candidatus", `{"scraper_type":"json-ld"}`, candidatusFixtureListing, queue.Browser)
			ctx := context.Background()
			claim, e := f.a.Claim(ctx, queue.Browser)
			if e != nil || claim == nil {
				t.Fatal("bound browser claim unavailable", e)
			}
			circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if e != nil {
				t.Fatal(e)
			}
			calls := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Host != "carrieres.candidatus.com" || r.URL.Path != "/site-emploi,ZmFrZQ" {
					t.Error("postback left publisher listing scope")
				}
				if r.Method == "GET" {
					http.SetCookie(w, &http.Cookie{Name: "listing", Value: "fresh", Path: "/"})
					body := candidatusFixtureHTML()
					if mode == "root-reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
					}
					if mode == "changed-listing" && calls == 3 {
						body = strings.ReplaceAll(body, "c-2-A20", "c-3-A20")
					}
					if mode == "foreign-action" {
						body = strings.Replace(body, `action="?id=ZmFrZQ`, `action="https://private.example/?id=ZmFrZQ`, 1)
					}
					fmt.Fprint(w, body)
					return
				}
				if r.ParseForm() != nil || r.PostForm.Get("WD_BUTTON_CLICK_") != "A20" || r.PostForm.Get("_A18_OCC") != "2" {
					t.Error("exact form state not preserved")
				}
				cookie, e := r.Cookie("listing")
				if e != nil || cookie.Value != "fresh" {
					t.Error("operation cookie not preserved")
				}
				if mode == "post-reserved" {
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(503)
					return
				}
				if mode == "later-failure" && calls == 4 {
					w.WriteHeader(503)
					return
				}
				if mode == "no-redirect" {
					fmt.Fprint(w, "incomplete response")
					return
				}
				location := "/annonce-emploi,job" + r.PostForm.Get("A18")
				if mode == "duplicate-detail" {
					location = "/annonce-emploi,same"
				}
				if mode == "foreign-redirect" {
					location = "https://private.example/annonce-emploi,job"
				}
				w.Header().Set("Location", location)
				w.WriteHeader(302)
			})
			renderer := &NativeRenderedDetails{client: &lp.Client{}, slots: make(chan struct{}, 1)}
			result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("postback claim did not settle", e)
			}
			assertRichDeadlineAndLease(t, f, "candidatus", queue.Browser)
			var inserted, missing, failures int
			var reserved bool
			if e := f.pg.QueryRow(ctx, `SELECT p.missing_count,b.consecutive_failures,b.tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=b.id AND id<>p.id) FROM job_posting p JOIN job_board b ON b.id=p.board_id WHERE p.id=$1::uuid`, f.original).Scan(&missing, &failures, &reserved, &inserted); e != nil {
				t.Fatal(e)
			}
			if mode == "complete" {
				if calls != 4 || inserted != 2 || missing != 1 || failures != 0 || reserved || f.r.ZCard(ctx, "ft_scrapes_simple:carrieres.candidatus.com").Val() != 2 {
					t.Fatal("complete original inventory not conserved", calls, inserted, missing, failures)
				}
			} else {
				want := 1
				optOut := strings.Contains(mode, "reserved")
				if optOut {
					want = 0
				}
				if inserted != 0 || missing != 0 || failures != want || reserved != optOut {
					t.Fatal("incomplete postbacks leaked prefix, absence or policy", inserted, missing, failures, reserved)
				}
			}
		})
	}
}
