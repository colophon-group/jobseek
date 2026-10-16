package worker

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestRealDetailConfigurationFailureKeepsProfileAndOriginStageWithoutSettlement(t *testing.T) {
	f, authority, claim := independentDetailOwnedFixture(t, `{"scraper_type":"json-ld","scraper_config":{"render":true}}`, "", queue.Browser)
	client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("configuration failure issued direct request") })
	var unavailable *NativeRenderedDetails
	renderer := heldRenderedDetail(func(ctx context.Context, profile queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
		return unavailable.Fetch(ctx, profile)
	})
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	result, err := RunDetail(context.Background(), authority, claim, client, richPipelinePreparer(t, f).Processor, circuits, renderer)
	var diagnostic *ClaimRunError
	if result == nil || result.Settled || result.Cycle != nil || !errors.Is(err, queue.ErrConfiguration) || !errors.As(err, &diagnostic) || diagnostic.Phase != "detail_execution" || diagnostic.causePhase != "detail_render_client" || diagnostic.detailProfile != "jsonld.rendered-detail/v1" {
		t.Fatal("fatal configuration diagnostic lost safe stage/profile or fabricated settlement")
	}
	if !strings.Contains(err.Error(), "cause_phase=detail_render_client profile=jsonld.rendered-detail/v1") || strings.Contains(err.Error(), f.original) || strings.Contains(err.Error(), "https://") {
		t.Fatal("diagnostic omitted bounded context or disclosed task identity")
	}
	var state string
	var descriptions, failures int
	if err := f.pg.QueryRow(context.Background(), `SELECT f.state,p.scrape_failures,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p JOIN ordinary_worker_write_fence f ON f.task_id=p.id AND f.task_kind='scrape' WHERE p.id=$1::uuid`, f.original).Scan(&state, &failures, &descriptions); err != nil {
		t.Fatal(err)
	}
	if state != "active" || failures != 0 || descriptions != 0 || f.r.ZCard(context.Background(), "inflight:browser").Val() != 1 {
		t.Fatal("configuration failure changed content, budget or retained recovery attempt")
	}
}
