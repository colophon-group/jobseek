package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealRemainingHTTPDetailCanonicalSettlement(t *testing.T) {
	for _, c := range remainingDetailCases(t) {
		if c.Mode != "complete" {
			continue
		}
		variants := []bool{false}
		if c.Provider == "headhunter" {
			variants = append(variants, true)
		}
		for _, proxy := range variants {
			for _, mode := range []string{"complete", "failed", "reserved", "wrong-id", "empty-404", "public-fallback"} {
				if mode == "public-fallback" && c.Provider != "headhunter" {
					continue
				}
				t.Run(fmt.Sprintf("%s/proxy-%t/%s", c.Provider, proxy, mode), func(t *testing.T) {
					options := map[string]any{}
					json.Unmarshal(c.Config, &options)
					if c.Provider == "headhunter" {
						options["proxy"] = proxy
					}
					md, _ := json.Marshal(map[string]any{"scraper_type": c.Provider, "scraper_config": options})
					source := c.Source
					if c.Provider == "johdi" {
						source = "https://careers.example.net/jobs#/offer/23/job"
					}
					f, owned, claim := independentDetailOwnedFixture(t, string(md), source)
					ctx := context.Background()
					selected, e := runtimeClaimUsesProxy(ctx, owned, claim)
					if e != nil || selected != proxy {
						t.Fatal("detail proxy boundary changed", e)
					}
					calls := 0
					client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						calls++
						w.Header().Set("Content-Type", "application/json")
						if mode == "reserved" {
							w.Header().Set("TDM-Reservation", "1")
							w.WriteHeader(503)
							return
						}
						if mode == "failed" {
							w.WriteHeader(400)
							return
						}
						if mode == "empty-404" {
							w.WriteHeader(404)
							return
						}
						if mode == "public-fallback" {
							if r.Host == "api.hh.ru" {
								w.WriteHeader(403)
								return
							}
							w.Header().Set("Content-Type", "text/html")
							fmt.Fprint(w, nativeJSONLDHTML)
							return
						}
						row := map[string]any{}
						for key, value := range c.Row {
							row[key] = value
						}
						if mode == "wrong-id" {
							row["id"] = 999
						}
						if json.NewEncoder(w).Encode(row) != nil {
							t.Error("fixture response failed")
						}
					}))
					if proxy {
						client = credentialedProxyFixture(t, client)
					}
					circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
					if e != nil {
						t.Fatal(e)
					}
					result, e := RunDetail(ctx, owned, claim, client, richPipelinePreparer(t, f).Processor, circuits)
					if e != nil || result == nil || !result.Settled {
						t.Fatal("detail did not settle", e)
					}
					var title, html string
					var active, reserved bool
					var due *time.Time
					var failures int
					if e = f.pg.QueryRow(ctx, `SELECT titles[1],is_active,tdm_reserved,next_scrape_at,scrape_failures,coalesce((SELECT html FROM descriptions d WHERE d.posting_id=p.id),'') FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &active, &reserved, &due, &failures, &html); e != nil {
						t.Fatal(e)
					}
					if mode == "complete" || mode == "public-fallback" {
						want := c.Expected["title"].(string)
						if mode == "public-fallback" {
							want = "Senior Software Engineer"
						}
						if title != want || html == "" || !active || reserved || due == nil || failures != 0 || result.Cycle.Status != "succeeded" {
							t.Fatal("canonical detail fields/schedule changed")
						}
					} else {
						if title != "Original" || html != "" {
							t.Fatal("unproved detail wrote content")
						}
						// HeadHunter returns empty JobContent on404. Its existing
						// empty-content failure does not grant navigation-gone authority.
						if !active {
							t.Fatal("failed/policy detail retired posting")
						}
						if mode == "reserved" && (!reserved || result.Cycle.Status != "publisher_reserved") {
							t.Fatal("publisher outcome changed")
						}
					}
					wantCalls := 1
					if mode == "public-fallback" {
						wantCalls = 2
					}
					if calls != wantCalls || f.r.ZCard(ctx, "inflight:simple").Val() != 0 || f.r.HLen(ctx, "inflight_tokens:simple").Val() != 0 || strings.Contains(result.Cycle.Status, "pending") {
						t.Fatal("detail request or claim conservation changed")
					}
				})
			}
		}
	}
}
