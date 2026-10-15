package worker

import (
	"context"
	"fmt"
	"net/http"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealJSONLDWorkdayRecoveryCanonicalWriterAndPolicy(t *testing.T) {
	for _, mode := range []string{"recovered", "primary-success", "reserved-search", "reserved-detail", "missing"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"scraper_type":"json-ld","scraper_config":{"workday_fallback":{"company":"fixture","wd_instance":"wd5","site":"External","proxy":true}}}`
			source := "https://example.com/job/REQ100/engineer"
			f, a, claim := independentDetailOwnedFixture(t, metadata, source)
			primaryCalls, fallbackCalls := 0, 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				primaryCalls++
				if mode == "primary-success" {
					fmt.Fprint(w, nativeJSONLDHTML)
					return
				}
				w.WriteHeader(http.StatusForbidden)
			})
			fallback := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				fallbackCalls++
				w.Header().Set("Content-Type", "application/json")
				if mode == "reserved-search" || mode == "reserved-detail" && r.Method == "GET" {
					w.Header().Set("TDM-Reservation", "1")
				}
				if r.Method == "POST" {
					if mode == "missing" {
						fmt.Fprint(w, `{"jobPostings":[]}`)
						return
					}
					fmt.Fprint(w, `{"jobPostings":[{"externalPath":"/job/NY/Engineer_REQ100"}]}`)
					return
				}
				fmt.Fprint(w, `{"jobPostingInfo":{"title":"Recovered Engineer","jobDescription":"<p>Complete recovery description.</p>"}}`)
			})
			fallback.proxyRequired = true
			client.workdayRecoveryProxy = fallback
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			ctx := context.Background()
			result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("recovery did not settle", err)
			}
			var title, storedSource string
			var reserved bool
			var due, lease *time.Time
			var descriptions int
			err = f.pg.QueryRow(ctx, `SELECT titles[1],source_url,tdm_reserved,next_scrape_at,leased_until,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &storedSource, &reserved, &due, &lease, &descriptions)
			if err != nil || storedSource != source || lease != nil || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("canonical identity or lease changed", err)
			}
			if mode == "reserved-search" || mode == "reserved-detail" {
				if !reserved || title != "Original" || descriptions != 0 {
					t.Fatal("recovery publisher policy changed content")
				}
			} else if mode == "missing" {
				if title != "Original" || descriptions != 0 || result.Cycle.Status == "succeeded" {
					t.Fatal("missing requisition attached content")
				}
			} else {
				want := "Recovered Engineer"
				if mode == "primary-success" {
					want = "Senior Software Engineer"
				}
				if result.Cycle.Status != "succeeded" || title != want || descriptions != 1 || due == nil {
					t.Fatal("recovery canonical write failed")
				}
				score, err := f.r.ZScore(ctx, "scrapes_simple:"+claim.Descriptor().Domain, f.original).Result()
				if err != nil || score != float64(due.UnixMicro())/1e6 {
					t.Fatal("recurring deadline differs", err)
				}
			}
			expectedPrimary, expectedFallback := 2, 2
			if mode == "primary-success" {
				expectedPrimary, expectedFallback = 1, 0
			}
			if mode == "reserved-search" || mode == "missing" {
				expectedFallback = 1
			}
			if primaryCalls != expectedPrimary || fallbackCalls != expectedFallback {
				t.Fatal("request ordering differs", primaryCalls, fallbackCalls)
			}
		})
	}
}
