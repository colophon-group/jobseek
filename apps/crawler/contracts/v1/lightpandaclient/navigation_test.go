package lightpandaclient

import (
	"bytes"
	"strings"
	"testing"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/browserproxy"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/proto"
)

func TestNavigationPreservesWaitFallbackAndOriginRetryAttribution(t *testing.T) {
	fallback := "domcontentloaded"
	n := Navigation{URL: "https://example.com/jobs/42", RoutingRevision: "fixture-v1", OriginRequestID: "ordinary-detail:fixture", Wait: "networkidle", WaitFallback: &fallback, TimeoutMS: 30000, TransportRetries: 1}
	input, err := NavigationInput(n)
	if err != nil {
		t.Fatal(err)
	}
	plan := input.Plan.Navigation
	if plan.WaitUntil != runtimev1.WaitCondition_WAIT_CONDITION_NETWORK_IDLE || plan.TimeoutMs != 30000 || plan.Fallback.WaitUntil != runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED || plan.Fallback.TimeoutMs != 5000 || plan.TransportRetries != 1 {
		t.Fatal("existing browser navigation defaults differ")
	}
	refs := input.Plan.OriginOperations
	if len(refs) != 2 || refs[0].Role != "navigation" || refs[1].Role != "transport_retry" || refs[1].GetParentOriginRequestId() != n.OriginRequestID || refs[1].RequestFingerprint != refs[0].RequestFingerprint || refs[1].OperationSequence != 2 {
		t.Fatal("origin retry attribution differs")
	}
	n.TimeoutMS = 2500
	n.TransportRetries = 0
	input, err = NavigationInput(n)
	if err != nil || input.Plan.Navigation.Fallback.TimeoutMs != 2500 || len(input.Plan.OriginOperations) != 1 {
		t.Fatal("bounded navigation fallback differs", err)
	}
	n.WaitFallback = nil
	input, err = NavigationInput(n)
	if err != nil || input.Plan.Navigation.Fallback != nil {
		t.Fatal("disabled fallback restored", err)
	}
	fallback = n.Wait
	n.WaitFallback = &fallback
	input, err = NavigationInput(n)
	if err != nil || input.Plan.Navigation.Fallback != nil {
		t.Fatal("identical fallback duplicated", err)
	}
}

func TestNavigationProxyWireContainsOnlyBoundReference(t *testing.T) {
	endpoint, err := browserproxy.Parse("http://fixture-user:fixture-secret@proxy.example:8000")
	if err != nil {
		t.Fatal(err)
	}
	config := Navigation{URL: "https://example.com/job", RoutingRevision: "proxy-fixture", OriginRequestID: "fixture-request", Wait: "load", TimeoutMS: 1000, ProxyPolicyRef: endpoint.Reference()}
	input, err := NavigationInput(config)
	if err != nil || input.Plan.GetSession().GetProxyPolicyRef() != endpoint.Reference() || len(input.Plan.RequiredCapabilities) != 2 {
		t.Fatal("navigation lost its explicit proxy binding")
	}
	wire, err := proto.Marshal(input)
	if err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range []string{"fixture-user", "fixture-secret", "proxy.example"} {
		if bytes.Contains(wire, []byte(forbidden)) {
			t.Fatal("navigation wire contains proxy configuration")
		}
	}
	for _, ref := range []string{"http://user:password@proxy.example", "crawler-proxy/v1:" + strings.Repeat("a", 65)} {
		config.ProxyPolicyRef = ref
		if _, err := NavigationInput(config); err == nil {
			t.Fatal("raw or unbounded proxy policy accepted")
		}
	}
}
