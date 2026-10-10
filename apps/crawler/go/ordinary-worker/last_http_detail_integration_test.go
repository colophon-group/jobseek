package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealLastHTTPPairedDetailSettlement(t *testing.T) {
	for _, provider := range []string{"infor", "peoplesoft"} {
		var cases []lastHTTPDetailCase
		b, err := os.ReadFile("../api-sniffer-monitor/testdata/python_last_" + provider + "_detail.json")
		if err != nil || json.Unmarshal(b, &cases) != nil {
			t.Fatal("original paired fixture unavailable")
		}
		c := cases[0]
		if provider == "peoplesoft" {
			body := c.Source
			c.Board = "https://fixture.example/psc/site/EMPLOYEE/HRMS/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL"
			o, err := api.LastHTTPOptionsFromMetadata(provider, c.Board, "{}")
			if err != nil {
				t.Fatal(err)
			}
			c.Source = o.PeopleSoftJobURL(c.ExpectedID)
			c.Exchanges = []lastHTTPExchange{{Method: "GET", URL: o.Origin + "/psp/site/EMPLOYEE/HRMS/?cmd=logout", Body: "<html>Anonymous context</html>"}, {Method: "GET", URL: c.Source, Body: body}}
		}
		for _, mode := range []string{"complete", "failed", "reserved", "invalid-document"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				metadata := `{"scraper_type":"` + provider + `","scraper_config":{}}`
				f, owned := independentDetailOwnedSetup(t, metadata, c.Source, queue.Simple, c.Board)
				ctx := context.Background()
				claim, err := owned.Claim(ctx, queue.Simple)
				if err != nil || claim == nil {
					t.Fatal("paired detail claim unavailable", err)
				}
				proxy, err := runtimeClaimUsesProxy(ctx, owned, claim)
				if err != nil || proxy {
					t.Fatal("paired session gained unconfigured proxy", err)
				}
				used := 0
				client := &VerifiedDirectHTTP{client: verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if mode == "failed" || mode == "reserved" {
						used++
						if mode == "reserved" {
							w.Header().Set("TDM-Reservation", "1")
						}
						w.WriteHeader(400)
						return
					}
					if used >= len(c.Exchanges) {
						t.Error("extra paired session request")
						w.WriteHeader(400)
						return
					}
					x := c.Exchanges[used]
					used++
					u, _ := url.Parse(x.URL)
					if r.Method != x.Method || r.URL.Path != u.Path {
						t.Error("paired request changed")
					}
					for k, v := range x.ResponseHeaders {
						if !strings.EqualFold(k, "content-length") {
							w.Header().Set(k, v)
						}
					}
					if mode == "invalid-document" && used == len(c.Exchanges) {
						fmt.Fprint(w, "<html>Missing required fields</html>")
						return
					}
					fmt.Fprint(w, x.Body)
				}))}
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				result, err := RunDetail(ctx, owned, claim, client, richPipelinePreparer(t, f).Processor, circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("paired detail did not settle", err)
				}
				var title, description string
				var active, reserved bool
				var due *time.Time
				var failures int
				if err := f.pg.QueryRow(ctx, `SELECT titles[1],is_active,tdm_reserved,next_scrape_at,scrape_failures,coalesce((SELECT html FROM descriptions d WHERE d.posting_id=p.id),'') FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &active, &reserved, &due, &failures, &description); err != nil {
					t.Fatal(err)
				}
				if mode == "complete" {
					if title != c.Job["title"] || description == "" || !active || reserved || due == nil || failures != 0 || result.Cycle.Status != "succeeded" {
						t.Fatal("complete paired fields and schedule changed")
					}
				} else {
					if title != "Original" || description != "" || !active {
						t.Fatal("unproved paired detail wrote content or absence")
					}
					if mode == "reserved" && (!reserved || result.Cycle.Status != "publisher_reserved") {
						t.Fatal("paired publisher outcome changed")
					}
				}
				want := 1
				if mode == "complete" || mode == "invalid-document" {
					want = len(c.Exchanges)
				}
				if used != want || f.r.ZCard(ctx, "inflight:simple").Val() != 0 || f.r.HLen(ctx, "inflight_tokens:simple").Val() != 0 {
					t.Fatal("paired detail requests or lease conservation changed", used, want)
				}
				u, _ := url.Parse(c.Source)
				score, err := f.r.ZScore(ctx, "scrapes_simple:"+u.Hostname(), f.original).Result()
				if err != nil || due == nil || score != float64(due.UnixMicro())/1e6 {
					t.Fatal("paired canonical recurring schedule lost", err)
				}
			})
		}
	}
}
