package queue

import "testing"

func TestRealWorkableProxyAndSmartCanonicalColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"workable/proxy", "smartrecruiters/job-v1", "smartrecruiters/job-location-v1", "smartrecruiters/template"}, firstProviderBatchFixture)
}
