package queue

import "testing"

func TestRealLocalizedHTTPMonitorColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"talemetry", "talemetry/proxy", "talemetry/json", "talemetry/json/proxy", "prospective", "kipt"}, firstProviderBatchFixture)
}
