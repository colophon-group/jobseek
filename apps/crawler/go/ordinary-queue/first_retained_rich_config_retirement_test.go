package queue

import "testing"

func TestRealRetainedRichConfigColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"ashby", "lever", "recruitee", "smartrecruiters"}, firstProviderBatchFixture)
}
