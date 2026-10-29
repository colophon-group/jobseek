package worker

import (
	"context"
	"errors"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealProxyBrowserMonitorRetainsRendererAndCanonicalSettlement(t *testing.T) {
	realProxyBrowserMonitorSettlement(t, false)
}
func TestRealDefaultProxyBrowserMonitorRetainsRendererAndCanonicalSettlement(t *testing.T) {
	realProxyBrowserMonitorSettlement(t, true)
}
func realProxyBrowserMonitorSettlement(t *testing.T, defaultOwner bool) {
	f := privateRichPipelineFixture(t, "dom", `{"render":true,"proxy":true,"url_filter":"/jobs/","scraper_type":"skip"}`, queue.Browser)
	if defaultOwner {
		useDefaultPipelineAuthority(t, &f)
	}
	ctx := context.Background()
	claim, err := f.a.Claim(ctx, queue.Browser)
	if err != nil || claim == nil {
		t.Fatal("owned browser claim unavailable", err)
	}
	proxy, err := runtimeClaimUsesProxy(ctx, f.a, claim)
	if err != nil || !proxy {
		t.Fatal("runtime did not select canonical browser proxy", err)
	}
	verified, _ := browserProxyFixture(t, 1)
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	// Reproduce the old runtime branch: selecting the proxy client discarded
	// the installed renderer, failing before any canonical work or network.
	_, err = RunGreenhouseClaim(ctx, f.a, claim, verified, &pipelinePreparer{}, circuits)
	var diagnostic *ClaimRunError
	if !errors.As(err, &diagnostic) || diagnostic.Phase != "startup" || diagnostic.Kind != "configuration" {
		t.Fatal("missing renderer no longer reproduces startup refusal")
	}
	calls := 0
	renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
		calls++
		if !p.BrowserProxy || p.Profile != "dom.rendered-urls/v1" {
			t.Fatal("canonical proxy authority changed")
		}
		return collectRenderedDOMPages(ctx, p, c, nil, func(int) (*runtimev1.BrowserResult, error) {
			return heldRenderedResult(`<a href="/jobs/one">Engineer</a>`, c["board_url"], 200), nil
		})
	})
	result, err := RunGreenhouseClaim(ctx, f.a, claim, verified, &pipelinePreparer{}, circuits, renderer)
	if err != nil || result == nil || !result.Settled || result.Cycle.Status != "succeeded" || calls != 1 {
		t.Fatal("proxy transport lost rendered execution or settlement", err)
	}
	var active int
	if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND is_active AND source_url LIKE '%/jobs/one'", f.board).Scan(&active); err != nil || active != 1 {
		t.Fatal("rendered proxy inventory did not reach canonical storage", err)
	}
	assertRichDeadlineAndLease(t, f, "dom", queue.Browser)
}
