package worker

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"testing"
)

func TestRealPostfinanceCanonicalMigrationThroughOrdinaryClaim(t *testing.T) {
	file, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	rows, err := csv.NewReader(file).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	columns := map[string]int{}
	for i, key := range rows[0] {
		columns[key] = i
	}
	var config map[string]any
	for _, row := range rows[1:] {
		if row[columns["board_slug"]] == "postfinance-careers" {
			if err := json.Unmarshal([]byte(row[columns["monitor_config"]]), &config); err != nil {
				t.Fatal(err)
			}
			config["scraper_type"] = "skip"
		}
	}
	if config == nil {
		t.Fatal("published board configuration missing")
	}
	config["_monitor_config_fingerprint"] = "94dd0b3abd85e29028d1819a805ce204c8eefcdb6d8695881bf6834168e994f1"
	config["recent_discovered_counts"] = []int{1, 1, 1}
	raw, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"complete", "partial_xml", "reserved", "unknown", "no_history"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixtureURL(t, "rss", string(raw), "https://jobs.postfinance.ch/search/?locale=de_DE")
			ctx := context.Background()
			legacy := "https://job.post.ch/PostFinance/job/old/42-de_DE"
			if mode == "unknown" {
				legacy = "https://unknown.invalid/job"
			}
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,source_identity=$2,board_id=NULL WHERE id=$1::uuid", f.original, legacy); err != nil {
				t.Fatal(err)
			}
			if mode == "no_history" {
				if _, err := f.pg.Exec(ctx, "UPDATE job_board SET metadata=metadata-'recent_discovered_counts' WHERE id=$1::uuid", f.board); err != nil {
					t.Fatal(err)
				}
			}
			claim, circuits := claimFixture(t, f)
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Host != "jobs.postfinance.ch" || r.URL.Path != "/googlefeed.xml" {
					t.Error("published feed request changed")
				}
				if mode == "reserved" {
					w.Header().Set("TDM-Reservation", "1")
					return
				}
				fmt.Fprint(w, `<rss xmlns:g="http://base.google.com/ns/1.0"><channel><item><link>https://job.post.ch/PostFinance/job/engineer/42/</link><title>PostFinance Software Engineer</title><description><![CDATA[<p>Build services in Go.</p>]]></description><guid>42</guid><g:location>Zurich</g:location></item></channel></rss>`)
				if mode == "partial_xml" {
					fmt.Fprint(w, "<broken")
				}
			}))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("ordinary migration cycle failed to settle", err)
			}
			assertRichDeadlineAndLease(t, f, "rss")
			var receipt json.RawMessage
			var active bool
			var failures int
			if err := f.pg.QueryRow(ctx, "SELECT metadata->'_identity_migration_receipt',consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&receipt, &failures); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				if active || len(receipt) == 0 || result.Cycle.Gone != 1 || result.Batches.Inserted != 1 || failures != 0 {
					t.Fatal("complete inventory lost canonical retirement", active, string(receipt), result.Cycle.Gone, result.Batches.Inserted, failures)
				}
				var count int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE company_id=$1::uuid AND source_url='https://jobs.postfinance.ch/job/_/42/' AND is_active", f.company).Scan(&count); err != nil || count != 1 {
					t.Fatal("canonical identity missing", err, count)
				}
			} else if len(receipt) > 0 || !active {
				t.Fatal("unqualified inventory retired identities", mode)
			}
			if failures != map[string]int{"partial_xml": 1}[mode] {
				t.Fatal("failure accounting changed", mode, failures)
			}
		})
	}
}
