package worker

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealDOMResponseFingerprintCanonicalQueueFailureAndPublisherPolicy(t *testing.T) {
	for _, mode := range []string{"complete", "invalid-validator", "reserved-validator"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"link_selector":"a.job","url_filter":"/jobs/","fingerprint_response":{"content_type":"application/pdf"},"scraper_type":"pdf","scraper_config":{"title_source":"text"}}`
			f := privateRichPipelineFixture(t, "dom", metadata)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, queue.Simple)
			if err != nil || claim == nil {
				t.Fatal(err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			var calls atomic.Int64
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if r.URL.Path == "/careers" {
					if r.Method != "GET" {
						t.Error("listing method differs")
					}
					fmt.Fprintf(w, `<a class="job" href="/jobs/%s/one.pdf">One</a><a class="job" href="/jobs/%s/two.pdf">Two</a>`, f.company, f.company)
					return
				}
				if r.Method != "HEAD" || !strings.HasPrefix(r.URL.Path, "/jobs/"+f.company+"/") {
					t.Error("unbound mutable document request", r.Method, r.URL.Path)
				}
				if mode == "reserved-validator" {
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(503)
					return
				}
				for key, values := range fingerprintHeaders() {
					w.Header()[key] = values
				}
				if mode == "invalid-validator" {
					w.Header().Del("ETag")
				}
				w.WriteHeader(200)
			}))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("mutable inventory did not settle", err, result)
			}
			assertRichDeadlineAndLease(t, f, "dom", queue.Simple)
			var inserted, missing, failures int
			var reserved bool
			if err := f.pg.QueryRow(ctx, `SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid`, f.board, f.original).Scan(&inserted); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, `SELECT missing_count FROM job_posting WHERE id=$1::uuid`, f.original).Scan(&missing); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, `SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid`, f.board).Scan(&failures, &reserved); err != nil {
				t.Fatal(err)
			}
			if mode != "complete" {
				status, expectedFailures := "failed", 1
				if mode == "reserved-validator" {
					status, expectedFailures = "publisher_reserved", 0
				}
				if inserted != 0 || missing != 0 || failures != expectedFailures || reserved != (mode == "reserved-validator") || result.Cycle.Status != status || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 0 {
					t.Fatal("failed HEAD changed canonical inventory or queues", inserted, missing, failures, reserved, result.Cycle.Status)
				}
				return
			}
			if inserted != 2 || missing != 1 || failures != 0 || reserved || calls.Load() != 3 || result.Cycle.Status != "succeeded" || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 2 {
				t.Fatal("complete mutable inventory lost canonical jobs or queue entries", inserted, missing, failures, reserved, calls.Load(), result.Cycle.Status)
			}
			rows, err := f.pg.Query(ctx, `SELECT id::text,source_url FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid`, f.board, f.original)
			if err != nil {
				t.Fatal(err)
			}
			defer rows.Close()
			for rows.Next() {
				var id, source string
				if err := rows.Scan(&id, &source); err != nil {
					t.Fatal(err)
				}
				u, err := url.Parse(source)
				if err != nil || len(u.Query().Get("_jobseek_fp")) != 24 || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
					t.Fatal("canonical and queued immutable identities differ")
				}
			}
			if err := rows.Err(); err != nil {
				t.Fatal(err)
			}
		})
	}
}
