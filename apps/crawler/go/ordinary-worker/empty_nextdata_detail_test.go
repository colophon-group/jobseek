package worker

import (
	"context"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"testing"
	"time"
)

func TestEmptyJOINNextdataMappingKeepsCanonicalContentWithoutHTTP(t *testing.T) {
	f, a := independentDetailOwnedSetup(t, `{"scraper_type":"nextdata"}`, "https://join.com/companies/native/123-engineer", queue.Simple, "https://join.com/companies/native")
	ctx := context.Background()
	claim, err := a.Claim(ctx, queue.Simple)
	if err != nil || claim == nil {
		t.Fatal(err)
	}
	client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Fatal("empty mapping issued HTTP") })
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
	if err != nil || result == nil || !result.Settled || result.HTTP.Requests != 0 {
		t.Fatal("no-request detail did not settle", err)
	}
	var title string
	var count int
	var due *time.Time
	var lease *time.Time
	if err = f.pg.QueryRow(ctx, `SELECT titles[1],(SELECT count(*) FROM descriptions WHERE posting_id=p.id),next_scrape_at,leased_until FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &count, &due, &lease); err != nil || title != "Original" || count != 0 {
		t.Fatal("empty result changed canonical content", err)
	}
	if due == nil || lease != nil {
		t.Fatal("empty result lost canonical deadline/lease")
	}
	score, err := f.r.ZScore(ctx, "scrapes_simple:"+claim.Descriptor().Domain, f.original).Result()
	if err != nil || score != float64(due.UnixMicro())/1e6 {
		t.Fatal("empty result queue deadline differs", err)
	}
	if f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("empty result retained lease")
	}
}
