package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"reflect"
	"strings"
	"sync"
	"testing"
)

func TestRealSmartCanonicalPreservesIdentityAliasesAndSettlement(t *testing.T) {
	for _, ref := range smartCanonicalCases(t) {
		if ref.Name != "canonical-bilingual-locations-fallback" && ref.Name != "localized-job-id" && ref.Name != "localized-template" {
			continue
		}
		for _, mode := range []string{"new", "move", "reserved-detail", "malformed-detail"} {
			t.Run(ref.Name+"/"+mode, func(t *testing.T) {
				md, _ := json.Marshal(ref.Metadata)
				f := privateRichPipelineFixtureURL(t, "smartrecruiters", string(md), ref.BoardURL)
				ctx := context.Background()
				first := ref.Expected.Jobs[0]
				identity, _ := first["source_identity"].(string)
				oldURL := "https://jobs.smartrecruiters.com/SwissMedicalNetwork1/OLD"
				if mode == "move" {
					if identity == "" {
						oldURL = first["url"].(string)
						identity = oldURL
					}
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_identity=$2,source_url=$3,next_scrape_at=NULL WHERE id=$1::uuid", f.original, identity, oldURL); err != nil {
						t.Fatal(err)
					}
				}
				claim, circuits := claimFixture(t, f)
				counts := map[string]int{}
				var mu sync.Mutex
				client := smartCanonicalHTTP(t, ref, counts, &mu)
				if strings.HasSuffix(mode, "detail") {
					client = verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						endpoint := "https://" + r.Host + r.URL.RequestURI()
						values := ref.Responses[endpoint]
						if len(values) == 0 {
							t.Error("unexpected request", endpoint)
							w.WriteHeader(400)
							return
						}
						if !strings.Contains(endpoint, "?") {
							if mode == "reserved-detail" {
								w.Header().Set("TDM-Reservation", "1")
								w.WriteHeader(404)
								return
							}
							fmt.Fprint(w, `{}`)
							return
						}
						_, _ = w.Write(values[0])
					}))
				}
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("canonical claim did not settle", result, err)
				}
				if strings.HasSuffix(mode, "detail") {
					var missing, failures int
					var reserved bool
					if err := f.pg.QueryRow(ctx, "SELECT p.missing_count,b.consecutive_failures,b.tdm_reserved FROM job_posting p JOIN job_board b ON b.id=p.board_id WHERE p.id=$1::uuid", f.original).Scan(&missing, &failures, &reserved); err != nil {
						t.Fatal(err)
					}
					if missing != 0 || reserved != (mode == "reserved-detail") || mode == "malformed-detail" && failures != 1 || result.Batches.Inserted+result.Batches.Touched != 0 {
						t.Fatal("failed/reserved inventory changed liveness or content", missing, failures, reserved, result)
					}
					assertRichDeadlineAndLease(t, f, "smartrecruiters")
					return
				}
				for _, want := range ref.Expected.Jobs {
					var id, storedIdentity string
					var titles, locales []string
					var descriptions int
					if err := f.pg.QueryRow(ctx, `SELECT p.id::text,p.source_identity,p.titles,p.locales,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE p.source_url=$1`, want["url"]).Scan(&id, &storedIdentity, &titles, &locales, &descriptions); err != nil {
						t.Fatal(err)
					}
					for key, actual := range map[string]any{"titles": titles, "locales": locales} {
						raw, _ := json.Marshal(actual)
						var normalized any
						json.Unmarshal(raw, &normalized)
						if !reflect.DeepEqual(normalized, want[key]) {
							t.Fatal("original canonical fields differ", key, actual, want[key])
						}
					}
					if descriptions == 0 {
						t.Fatal("rich description was not queued for upload")
					}
					if explicit, ok := want["source_identity"].(string); ok && storedIdentity != explicit {
						t.Fatal("durable source identity lost")
					}
					if mode == "move" && want["url"] == first["url"] && id != f.original {
						t.Fatal("publication URL change replaced canonical posting id")
					}
				}
				if mode == "move" && first["source_identity"] != nil {
					var alias string
					if err := f.pg.QueryRow(ctx, "SELECT posting_id::text FROM job_posting_source_alias WHERE source_url=$1", oldURL).Scan(&alias); err != nil || alias != f.original {
						t.Fatal("prior destination alias not preserved", alias, err)
					}
				}
				assertRichDeadlineAndLease(t, f, "smartrecruiters")
			})
		}
	}
}
