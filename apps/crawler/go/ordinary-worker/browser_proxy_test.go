package worker

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"testing"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/browserproxy"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
)

func browserProxyFixture(t *testing.T, size int) (*VerifiedHTTP, *proxyPool) {
	t.Helper()
	pool, err := newProxyPool(size, -1, func() float64 { return 0 })
	if err != nil {
		t.Fatal(err)
	}
	endpoints := make([]*url.URL, size)
	for i := range endpoints {
		endpoint, err := url.Parse("http://fixture:secret@proxy.example:" + []string{"8001", "8002"}[i])
		if err != nil {
			t.Fatal(err)
		}
		endpoints[i] = endpoint
	}
	return &VerifiedHTTP{proxyRequired: true, client: &http.Client{Transport: &rotatingProxyTransport{pool: pool, endpoints: endpoints}}}, pool
}

func TestBrowserProxyUsesExistingHTTPPoolAndCredentialBoundReference(t *testing.T) {
	verified, pool := browserProxyFixture(t, 2)
	newInput := func() *lp.Navigation {
		return &lp.Navigation{URL: "https://example.com/job", RoutingRevision: "fixture", OriginRequestID: "fixture-job", Wait: "load", TimeoutMS: 1000}
	}
	input, _ := lp.NavigationInput(*newInput())
	lease, err := bindBrowserProxy(input, map[string]any{"proxy": true}, verified)
	if err != nil || lease.selection.pool != pool || lease.selection.slot != 0 {
		t.Fatal("browser did not share HTTP pool selection")
	}
	endpoint, _ := browserproxy.Parse("http://fixture:secret@proxy.example:8001")
	if input.Plan.Session.GetProxyPolicyRef() != endpoint.Reference() {
		t.Fatal("browser proxy reference differs from protected HTTP endpoint")
	}
	// An HTTP-origin quarantine immediately affects browser selection.
	pool.failure(lease.selection, "example.com", "origin_block")
	lease.abandon()
	input, _ = lp.NavigationInput(*newInput())
	second, err := bindBrowserProxy(input, map[string]any{"proxy": true}, verified)
	if err != nil || second.selection.slot != 1 {
		t.Fatal("browser ignored shared HTTP origin quarantine")
	}
	second.observe(context.Background(), heldRenderedResult("<html>blocked</html>", input.Plan.TargetUrl, 403), nil)
	if _, err := pool.selectEndpoint("example.com"); !errors.Is(err, errProxyPoolExhausted) {
		t.Fatal("HTTP pool ignored browser origin quarantine")
	}
	// Deferred cleanup after an observed result must not settle the lease twice.
	second.abandon()
	if pool.origin(1, "example.com", false).failures != 1 {
		t.Fatal("browser proxy lease settled more than once")
	}
}

func TestBrowserProxyHealthRequiresCompleteValidatedResponse(t *testing.T) {
	for _, status := range []uint32{200, 401, 403, 407, 429, 503} {
		verified, pool := browserProxyFixture(t, 1)
		input, _ := lp.NavigationInput(lp.Navigation{URL: "https://example.com/job", RoutingRevision: "fixture", OriginRequestID: "fixture-job", Wait: "load", TimeoutMS: 1000})
		lease, err := bindBrowserProxy(input, map[string]any{"proxy": true}, verified)
		if err != nil {
			t.Fatal(err)
		}
		lease.observe(context.Background(), heldRenderedResult("<html>complete</html>", input.Plan.TargetUrl, status), nil)
		if (pool.global[0].failures == 1) != (status == 407) {
			t.Fatal("proxy authentication failure has incorrect scope")
		}
		origin := pool.origin(0, "example.com", false)
		blocked := status == 401 || status == 403 || status == 429
		if (origin != nil && origin.failures == 1) != blocked {
			t.Fatal("origin status has incorrect quarantine scope")
		}
	}
	verified, pool := browserProxyFixture(t, 1)
	input, _ := lp.NavigationInput(lp.Navigation{URL: "https://example.com/job", RoutingRevision: "fixture", OriginRequestID: "fixture-job", Wait: "load", TimeoutMS: 1000})
	lease, _ := bindBrowserProxy(input, map[string]any{"proxy": true}, verified)
	result := heldRenderedResult("<html>incomplete</html>", input.Plan.TargetUrl, 407)
	result.GetSuccess().Html.Complete = false
	lease.observe(context.Background(), result, nil)
	if pool.global[0].failures != 0 {
		t.Fatal("incomplete output changed endpoint health")
	}
}

func TestBrowserProxyRequiresSealedStartupChoice(t *testing.T) {
	input, _ := lp.NavigationInput(lp.Navigation{URL: "https://example.com/job", RoutingRevision: "fixture", OriginRequestID: "fixture-job", Wait: "load", TimeoutMS: 1000})
	if _, err := bindBrowserProxy(input, map[string]any{"proxy": true}, nil); err == nil {
		t.Fatal("required proxy silently became direct")
	}
	disabled := &VerifiedHTTP{proxyRequired: true, client: &http.Client{Transport: &directTransport{}}}
	if lease, err := bindBrowserProxy(input, map[string]any{"proxy": true}, disabled); err != nil || lease != nil || input.Plan.Session != nil {
		t.Fatal("explicit disabled provider changed original direct behavior")
	}
	if lease, err := bindBrowserProxy(input, map[string]any{"proxy": false}, nil); err != nil || lease != nil || input.Plan.Session != nil {
		t.Fatal("direct operation acquired proxy state")
	}
}
