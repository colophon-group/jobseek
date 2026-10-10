package queue

import "testing"

func TestRealRemainingHTTPMonitorColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"johdi", "jobdiva", "headhunter", "headhunter/proxy"}, firstProviderBatchFixture)
}

func TestRealRemainingHTTPDetailColdRetirement(t *testing.T) {
	for _, provider := range []string{"johdi", "headhunter", "headhunter/proxy"} {
		t.Run(provider, func(t *testing.T) { testFirstAPIDetailRetirement(t, provider) })
	}
}
