package worker

import (
	"context"
	"fmt"
	q "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"os"
	"strings"
	"testing"
)

func TestRealCompiledInlineCardsRefusePartialCanonicalOutput(t *testing.T) {
	metadata, err := os.ReadFile("../ordinary-queue/testdata/inline_mediamarkt_metadata.json")
	if err != nil {
		t.Fatal(err)
	}
	const boardURL = "https://deutsche-technikberatung.my.site.com/dtbjobs/s/bewerberseite-zentrale"
	var source strings.Builder
	ids := []string{}
	for i := 0; i < 5; i++ {
		ids = append(ids, fmt.Sprintf("a7u%015d", i))
		fmt.Fprintf(&source, `<jobseek-inline-detail>__inline_detail_boundary__<p>Fixture Engineer %d</p><p>Location: Berlin, Germany</p><p>Complete engineering description %d</p></jobseek-inline-detail>`, i, i)
	}
	for _, mode := range []string{"success", "missing-identity", "duplicate-identity", "truncated", "reserved"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixtureURL(t, "inline", string(metadata), boardURL, q.Browser)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, q.Browser)
			if err != nil || claim == nil {
				t.Fatal("compiled card claim", err)
			}
			circuits, err := q.NewHostCircuits(f.client, q.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			renderer := heldMonitor(func(ctx context.Context, p q.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
				value := heldRenderedResult(source.String(), boardURL, 200)
				identities := append([]string(nil), ids...)
				switch mode {
				case "missing-identity":
					identities = identities[:4]
				case "duplicate-identity":
					identities[4] = identities[0]
				case "truncated":
					value.GetSuccess().Html.Complete = false
				case "reserved":
					flag := "1"
					value.GetSuccess().ResourcePolicy.TdmReservationHeader = &flag
					identities = nil
				}
				return parseHeldRenderedMonitorWithInlineIdentities(ctx, p, c, value, identities)
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("card collection issued direct HTTP") })
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
			if err != nil || !result.Settled {
				t.Fatal("card settlement", err)
			}
			var count, descriptions int
			var reserved bool
			if err := f.pg.QueryRow(ctx, `SELECT count(*) FROM job_posting WHERE board_id=$1::uuid`, f.board).Scan(&count); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, `SELECT count(*) FROM descriptions d JOIN job_posting p ON p.id=d.posting_id WHERE p.board_id=$1::uuid`, f.board).Scan(&descriptions); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, `SELECT tdm_reserved FROM job_board WHERE id=$1::uuid`, f.board).Scan(&reserved); err != nil {
				t.Fatal(err)
			}
			if mode == "success" {
				if count != 6 || descriptions != 5 || result.Batches.Inserted != 5 || result.Cycle.Status != "succeeded" {
					t.Fatal("complete cards not persisted", count, descriptions, result)
				}
			} else if count != 1 || descriptions != 0 || reserved != (mode == "reserved") {
				t.Fatal("partial or reserved cards changed canonical output", count, descriptions)
			}
			assertRichDeadlineAndLease(t, f, "inline", q.Browser)
			if f.r.ZCard(ctx, "inflight:browser").Val() != 0 {
				t.Fatal("card inflight leaked")
			}
		})
	}
}
