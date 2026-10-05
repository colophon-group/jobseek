package lightpandaclient

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strconv"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

// Navigation contains caller-validated request controls, without queue authority.
type Navigation struct {
	URL, RoutingRevision, OriginRequestID, Wait string
	WaitFallback                                *string
	TimeoutMS                                   uint64
	TransportRetries                            uint32
}

func NavigationInput(c Navigation) (*runtimev1.BrowserExecutionInput, error) {
	waits := map[string]runtimev1.WaitCondition{
		"commit":           runtimev1.WaitCondition_WAIT_CONDITION_COMMIT,
		"domcontentloaded": runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED,
		"load":             runtimev1.WaitCondition_WAIT_CONDITION_LOAD,
		"networkidle":      runtimev1.WaitCondition_WAIT_CONDITION_NETWORK_IDLE,
	}
	condition, ok := waits[c.Wait]
	if !ok || c.TimeoutMS < 1 || c.TimeoutMS > 120000 || c.TransportRetries > 1 || c.OriginRequestID == "" {
		return nil, errors.New("invalid navigation controls")
	}
	payload, err := b0task.CanonicalJSON(map[string]any{"body": "", "headers": []any{}, "method": "GET", "url": c.URL}, true)
	if err != nil {
		return nil, err
	}
	fingerprint := sha256.Sum256(payload)
	navigation := &runtimev1.NavigationPlan{WaitUntil: condition, TimeoutMs: c.TimeoutMS, OriginRequestId: c.OriginRequestID, TransportRetries: c.TransportRetries}
	if c.WaitFallback != nil && *c.WaitFallback != c.Wait {
		fallback, ok := waits[*c.WaitFallback]
		if !ok {
			return nil, errors.New("invalid navigation fallback")
		}
		navigation.Fallback = &runtimev1.NavigationFallback{WaitUntil: fallback, TimeoutMs: min(c.TimeoutMS, 5000)}
	}
	operations := []*runtimev1.OriginOperationRef{{OriginRequestId: c.OriginRequestID, OperationSequence: 1, Role: "navigation", RequestFingerprint: hex.EncodeToString(fingerprint[:])}}
	for retry := uint32(1); retry <= c.TransportRetries; retry++ {
		operations = append(operations, &runtimev1.OriginOperationRef{OriginRequestId: c.OriginRequestID + ":transport-retry-" + strconv.Itoa(int(retry)), OperationSequence: retry + 1, Role: "transport_retry", ParentOriginRequestId: &c.OriginRequestID, RequestFingerprint: operations[0].RequestFingerprint})
	}
	return &runtimev1.BrowserExecutionInput{
		Assignment: &runtimev1.BrowserAssignment{Backend: runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA, CapabilityClass: runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION, ServiceLane: runtimev1.BrowserServiceLane_BROWSER_SERVICE_LANE_LIGHTPANDA, RoutingRevision: c.RoutingRevision},
		Plan:       &runtimev1.BrowserPlan{ContractVersion: runtimeContract, TargetUrl: c.URL, RequiredCapabilities: []runtimev1.BrowserCapability{runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER}, Navigation: navigation, OriginOperations: operations},
	}, nil
}
