package worker

import "testing"

func TestWorkdayPublishedUnderscoreTenantRetainsOtherHostValidation(t *testing.T) {
	const host = "vhr_wachtelllipton.wd1.myworkdayjobs.com"
	if got, e := directHost(host); e != nil || got != host {
		t.Fatal("published ASCII tenant rejected", e)
	}
	for _, bad := range []string{"private_host.example.com", "tenant_.wdx.myworkdayjobs.com", "tenant_.wd1.evil.com", "tenant_.wd1.myworkdayjobs.com.evil.com", "ténant_.wd1.myworkdayjobs.com", "tenant_@.wd1.myworkdayjobs.com"} {
		if _, e := directHost(bad); e == nil {
			t.Error("unqualified underscore host accepted", bad)
		}
	}
}
