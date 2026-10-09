package queue

import "testing"

func TestRealGroupedHTTPProvidersColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"curately", "inploi", "jobconvo"}, firstProviderBatchFixture)
}
