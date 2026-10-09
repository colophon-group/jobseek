package queue

import "testing"

func TestRealPortalHTTPProvidersColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"pageup", "infoniqa", "keka", "turbohire"}, firstProviderBatchFixture)
}
