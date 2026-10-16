package lightpandaadapter

import (
	"context"
	"testing"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/browserproxy"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

func configuredProxyInput(t *testing.T) *runtimev1.BrowserExecutionInput {
	t.Helper()
	endpoint, err := browserproxy.Parse("http://fixture:password@proxy.example:8000")
	if err != nil {
		t.Fatal(err)
	}
	input := navigationInput()
	ref := endpoint.Reference()
	input.Plan.Session = &runtimev1.SessionPlan{ProxyPolicyRef: &ref}
	input.Plan.RequiredCapabilities = append(input.Plan.RequiredCapabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY)
	return input
}

func TestConfiguredProxyBindsEntireSessionBeforeRunner(t *testing.T) {
	input := configuredProxyInput(t)
	want := input.Plan.Session.GetProxyPolicyRef()
	runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
		copy := bound.Input()
		if copy.Plan.Session.GetProxyPolicyRef() != want {
			t.Fatal("proxy reference lost its binding")
		}
		*copy.Plan.Session.ProxyPolicyRef = "changed"
		if bound.Input().Plan.Session.GetProxyPolicyRef() != want {
			t.Fatal("proxy session alias escaped")
		}
		return NewRunnerFailure(bound, ProviderFailure{Code: runtimev1.ErrorCode_ERROR_CODE_TRANSPORT, Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY})
	}}
	adapter, err := NewConfiguredProxyNavigationRenderOnly(runner)
	if err != nil {
		t.Fatal(err)
	}
	adapter.Execute(context.Background(), input)
	if runner.calls != 1 {
		t.Fatal("valid proxy plan never reached configured runner")
	}
	plain, _ := NewNavigationRenderOnly(runner)
	plain.Execute(context.Background(), input)
	if runner.calls != 1 {
		t.Fatal("unconfigured adapter accepted proxy transport")
	}
}

func TestConfiguredProxyRejectsSessionExpansionBeforeRunner(t *testing.T) {
	for _, change := range []func(*runtimev1.BrowserExecutionInput){
		func(i *runtimev1.BrowserExecutionInput) { i.Plan.Session.Persistent = true },
		func(i *runtimev1.BrowserExecutionInput) { i.Plan.Session.HeadfulIdentity = true },
		func(i *runtimev1.BrowserExecutionInput) { empty := ""; i.Plan.Session.SessionKey = &empty },
		func(i *runtimev1.BrowserExecutionInput) {
			raw := "http://user:secret@proxy.example"
			i.Plan.Session.ProxyPolicyRef = &raw
		},
		func(i *runtimev1.BrowserExecutionInput) { i.Plan.Session.ProxyPolicyRef = nil },
		func(i *runtimev1.BrowserExecutionInput) { i.Plan.Session = nil },
		func(i *runtimev1.BrowserExecutionInput) {
			i.Plan.RequiredCapabilities = i.Plan.RequiredCapabilities[:1]
		},
		func(i *runtimev1.BrowserExecutionInput) {
			i.Plan.RequiredCapabilities = append(i.Plan.RequiredCapabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY)
		},
	} {
		runner := &fakeRunner{run: func(context.Context, BoundInput) RunnerOutcome {
			t.Fatal("invalid proxy session reached origin runner")
			return RunnerOutcome{}
		}}
		adapter, _ := NewConfiguredProxyNavigationRenderOnly(runner)
		input := configuredProxyInput(t)
		change(input)
		adapter.Execute(context.Background(), input)
		if runner.calls != 0 {
			t.Fatal("invalid configured session was executed")
		}
	}
}

func TestConfiguredProxyFailureHTTPStatusIsCopiedAndScoped(t *testing.T) {
	for _, test := range []struct {
		configured bool
		code       runtimev1.ErrorCode
		status     uint32
		accepted   bool
	}{
		{true, runtimev1.ErrorCode_ERROR_CODE_HTTP_STATUS, 407, true},
		{false, runtimev1.ErrorCode_ERROR_CODE_HTTP_STATUS, 407, false},
		{true, runtimev1.ErrorCode_ERROR_CODE_HTTP_STATUS, 403, false},
		{true, runtimev1.ErrorCode_ERROR_CODE_TRANSPORT, 407, false},
	} {
		status := test.status
		runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
			outcome := NewRunnerFailure(bound, ProviderFailure{Code: test.code, Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY, HTTPStatus: &status})
			status = 500
			return outcome
		}}
		input := navigationInput()
		adapter, _ := NewNavigationRenderOnly(runner)
		if test.configured {
			input = configuredProxyInput(t)
			adapter, _ = NewConfiguredProxyNavigationRenderOnly(runner)
		}
		result := adapter.Execute(context.Background(), input)
		failure := result.GetError().GetError()
		if test.accepted {
			if failure.GetCode() != runtimev1.ErrorCode_ERROR_CODE_HTTP_STATUS || failure.GetHttpStatus() != 407 || failure.Message != "lightpanda execution failed" {
				t.Fatal("typed proxy auth status was mutated or disclosed text")
			}
		} else if failure.GetCode() != runtimev1.ErrorCode_ERROR_CODE_INTERNAL || failure.HttpStatus != nil {
			t.Fatal("unscoped provider status crossed failure boundary")
		}
	}
}
