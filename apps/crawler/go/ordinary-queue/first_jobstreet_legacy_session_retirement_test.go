package queue

import "testing"

func TestRealJobStreetAndLegacySessionColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"jobstreet", "rss/legacy-session"}, firstProviderBatchFixture)
}
