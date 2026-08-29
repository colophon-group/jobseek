package harness

import (
	"testing"

	"github.com/chromedp/cdproto/network"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

func TestRobotsLongestRuleWins(t *testing.T) {
	t.Parallel()
	policy := ParseRobots("User-agent: *\nDisallow: /private\nAllow: /private/public\n")
	if policy.Allows("/private/item") {
		t.Fatal("private path must be blocked")
	}
	if !policy.Allows("/private/public/item") {
		t.Fatal("longer allow rule must win")
	}
}

func TestTDMHeaderPrecedesMeta(t *testing.T) {
	t.Parallel()
	html := `<meta name="tdm-reservation" content="1">`
	if TDMReserved("0", html) {
		t.Fatal("conformant header zero must override meta")
	}
	if !TDMReserved("", html) {
		t.Fatal("meta reservation must block without a conformant header")
	}
	if !TDMReserved("1", "") {
		t.Fatal("header reservation must block")
	}
}

func TestValidatePlanReturnsTypedUnsupported(t *testing.T) {
	t.Parallel()
	plan := &runtimev1.BrowserPlan{
		ContractVersion: ContractVersion,
		TargetUrl:       "http://fixture:8080/navigation",
		RequiredCapabilities: []runtimev1.BrowserCapability{
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES,
		},
	}
	result, err := ValidatePlan(plan, "http://fixture:8080")
	if err != nil {
		t.Fatalf("validate: %v", err)
	}
	if result.GetUnsupported() == nil {
		t.Fatal("expected typed unsupported result")
	}
	got := result.GetUnsupported().GetCapabilities()
	if len(got) != 1 || got[0] != runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES {
		t.Fatalf("unexpected capabilities: %v", got)
	}
}

func TestValidatePlanRejectsExternalOrigin(t *testing.T) {
	t.Parallel()
	plan := &runtimev1.BrowserPlan{
		ContractVersion:      ContractVersion,
		TargetUrl:            "https://example.com/",
		RequiredCapabilities: []runtimev1.BrowserCapability{runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER},
	}
	if _, err := ValidatePlan(plan, "http://fixture:8080"); err == nil {
		t.Fatal("external origin must be rejected")
	}
}

func TestEverySubrequestUsesOriginAndRobotsPolicy(t *testing.T) {
	t.Parallel()
	state := &executionState{
		allowedOrigin: FixtureOrigin,
		robots:        ParseRobots("User-agent: *\nDisallow: /blocked\n"),
	}
	if path, reason := state.authorizeURL("http://fixture:8080/static/app.js"); path != "/static/app.js" || reason != "" {
		t.Fatalf("synthetic subrequest rejected: path=%q reason=%q", path, reason)
	}
	if _, reason := state.authorizeURL("http://fixture:8080/blocked/app.js"); reason != "robots_disallowed" {
		t.Fatalf("robots rule not enforced: %q", reason)
	}
	if _, reason := state.authorizeURL("http://outside:8080/app.js"); reason != "external_origin" {
		t.Fatalf("external origin not blocked: %q", reason)
	}
}

func TestResponseHeaderPolicyIsFailClosedForEveryResource(t *testing.T) {
	t.Parallel()
	state := &executionState{
		limits:       Limits{MaxRequests: 8, MaxResponseBytes: 1024},
		ledger:       []LedgerEntry{{Method: "GET", Path: "/static/tdm.js", Decision: "allowed"}},
		requestIndex: map[network.RequestID]int{"subresource": 0},
	}
	state.handleResponse(&network.EventResponseReceived{
		RequestID: "subresource",
		Type:      network.ResourceTypeScript,
		Response: &network.Response{
			Status:  200,
			Headers: network.Headers{"Content-Length": "32", "TDM-Reservation": "1"},
		},
	})
	if failure := state.getFailure(); failure == nil || failure.GetError().GetError().GetCode() != runtimev1.ErrorCode_ERROR_CODE_TDM_RESERVED {
		t.Fatalf("expected typed subresource TDM failure, got %v", failure)
	}
}

func TestDeclaredResponseBytesAreRejectedBeforeBodyCompletion(t *testing.T) {
	t.Parallel()
	state := &executionState{
		limits:        Limits{MaxRequests: 8, MaxResponseBytes: 1024},
		ledger:        []LedgerEntry{{Method: "GET", Path: "/static/large.js", Decision: "allowed"}},
		requestIndex:  map[network.RequestID]int{"large": 0},
		responseBytes: 100,
	}
	state.handleResponse(&network.EventResponseReceived{
		RequestID: "large",
		Type:      network.ResourceTypeScript,
		Response: &network.Response{
			Status:  200,
			Headers: network.Headers{"Content-Length": "1000"},
		},
	})
	failure := state.getFailure()
	if failure == nil || failure.GetError().GetError().GetCode() != runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT {
		t.Fatalf("expected typed byte-limit failure, got %v", failure)
	}
	if got := state.ledger[0].Reason; got != "declared_response_byte_limit" {
		t.Fatalf("missing ledger reason: %q", got)
	}
}
