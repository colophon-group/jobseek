package queue

import "testing"

func TestRealAmazonAndYumColdRetirementAndConservation(t *testing.T) {
	testProviderColdRetirement(t, []string{"amazon"}, firstProviderBatchFixture)
	testProviderColdRetirement(t, []string{"nextdata"}, firstNativeBrowserProviderFixture)
}
