package queue

import "testing"

func TestRealJarviAndJob51ColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"jarvi", "job51"}, firstProviderBatchFixture)
}
