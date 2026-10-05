package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestRealNextdataDurableIdentityPreservesPostingAliasesAndDetailSchedule(t *testing.T) {
	for _, mode := range []string{"new", "move", "promote-alias", "conflict", "future-detail", "cross-chunk", "employer", "employer-reserved", "employer-other"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"path":"jobs","url_template":"{url}","fields":{"title":"title"},"url_allowlist":"https://example\\.com/jobs/[A-Za-z0-9/-]+","source_identity":{"provider":"fixture","tenant":"test","field":"id"},"scraper_type":"json-ld"}`
			if mode == "future-detail" {
				metadata = strings.TrimSuffix(metadata, "}") + `,"scraper_config":{"enrich":["description"]}}`
			}
			if mode == "cross-chunk" {
				metadata = strings.TrimSuffix(metadata, "}") + `,"pagination":{"path":"pagination","page_count":"pages"}}`
			}
			if strings.HasPrefix(mode, "employer") {
				metadata = strings.TrimSuffix(metadata, "}") + `,"expected_hiring_organization":"Fixture Hospital"}`
			}
			f := privateRichPipelineFixture(t, "nextdata", metadata)
			ctx := context.Background()
			identity := "fixture:test:" + f.company
			url := "https://example.com/jobs/current/" + f.company
			oldURL := "https://example.com/jobs/prior/" + f.company
			var due time.Time
			if mode != "new" && mode != "cross-chunk" {
				oldIdentity := identity
				if mode == "conflict" {
					oldIdentity = "fixture:test:other"
					oldURL = url
				}
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_identity=$2,source_url=$3,description_r2_hash=123,next_scrape_at=NULL WHERE id=$1::uuid", f.original, oldIdentity, oldURL); err != nil {
					t.Fatal(err)
				}
				if mode == "promote-alias" {
					if _, err := f.pg.Exec(ctx, "INSERT INTO job_posting_source_alias(source_url,posting_id) VALUES($1,$2::uuid)", url, f.original); err != nil {
						t.Fatal(err)
					}
				}
				if mode == "future-detail" {
					due = time.Now().UTC().Add(time.Hour).Truncate(time.Microsecond)
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET next_scrape_at=$2 WHERE id=$1::uuid", f.original, due); err != nil {
						t.Fatal(err)
					}
					if err := f.r.HSet(ctx, "scrape:"+f.original, "source_url", oldURL, "board_id", f.board, "description_r2_hash", "123", "domain", "example.com", "scrape_step", "0").Err(); err != nil {
						t.Fatal(err)
					}
				}
			}
			claim, circuits := claimFixture(t, f)
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if strings.HasPrefix(r.URL.Path, "/jobs/") {
					if mode == "employer-reserved" {
						w.Header().Set("TDM-Reservation", "1")
					}
					name := "Fixture Hospital"
					if mode == "employer-other" {
						name = "Other Hospital"
					}
					fmt.Fprintf(w, `<script type="application/ld+json">{"@type":"JobPosting","hiringOrganization":{"name":"%s"}}</script>`, name)
					return
				}
				current := url
				if r.URL.Query().Get("page") == "2" {
					current = "https://example.com/jobs/second/" + f.company
				}
				fmt.Fprintf(w, `<script id="__NEXT_DATA__">{"jobs":[{"id":"%s","url":"%s","title":"Senior Software Engineer"}],"pagination":{"pages":2}}</script>`, f.company, current)
			})
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal(result, err)
			}
			if mode == "employer-reserved" || mode == "employer-other" {
				var reserved bool
				if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved); err != nil || reserved != (mode == "employer-reserved") {
					t.Fatal("detail employer policy differs", err, reserved)
				}
				if result.Batches.Inserted+result.Batches.Touched != 0 {
					t.Fatal("unproved employer wrote content", result)
				}
				return
			}
			if mode == "conflict" || mode == "cross-chunk" {
				var failures int
				if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures); err != nil || failures != 1 {
					t.Fatal("identity conflict was not an ordinary failure", err, failures)
				}
				var missing int
				if err := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); err != nil || missing != 0 {
					t.Fatal("failed identity stream delisted unseen posting", err, missing)
				}
				if mode == "conflict" && result.Batches.Inserted != 0 || mode == "cross-chunk" && result.Batches.Inserted != 1 {
					t.Fatal("identity conflict lost atomicity or committed prefix", result)
				}
				return
			}
			var id, storedURL string
			var storedDue *time.Time
			if err := f.pg.QueryRow(ctx, "SELECT id::text,source_url,next_scrape_at FROM job_posting WHERE source_identity=$1", identity).Scan(&id, &storedURL, &storedDue); err != nil || storedURL != url {
				t.Fatal("identity insert/move failed", err)
			}
			if mode == "new" {
				if result.Batches.Inserted != 1 {
					t.Fatal(result)
				}
				return
			}
			if id != f.original || result.Batches.Touched != 1 {
				t.Fatal("URL move replaced canonical posting", id, result)
			}
			var aliasOwner string
			if err := f.pg.QueryRow(ctx, "SELECT posting_id::text FROM job_posting_source_alias WHERE source_url=$1", oldURL).Scan(&aliasOwner); err != nil || aliasOwner != id {
				t.Fatal("old URL alias lost owner", err)
			}
			var currentAliases int
			if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting_source_alias WHERE source_url=$1", url).Scan(&currentAliases); err != nil || currentAliases != 0 {
				t.Fatal("promoted URL retained alias", err)
			}
			if mode == "future-detail" {
				if storedDue == nil || !storedDue.Equal(due) || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != url {
					t.Fatal("URL move lost canonical detail deadline/cache")
				}
				score, err := f.r.ZScore(ctx, "scrapes_simple:example.com", id).Result()
				if err != nil || score != float64(due.UnixMicro())/1e6 {
					t.Fatal("future detail changed priority", score, err)
				}
			}
		})
	}
}
