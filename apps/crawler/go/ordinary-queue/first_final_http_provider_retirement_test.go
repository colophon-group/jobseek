package queue

import "testing"

func TestRealFinalHTTPProvidersColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"paynet", "nowhiring", "fenbi", "wecruit"}, firstProviderBatchFixture)
}
