package apisniffer

import "testing"

func TestThirdProviderOptionsBoundResourcesAndIdentity(t *testing.T) {
	for _, raw := range []string{`{"tenant":"other"}`, `{"tenant":"acme","listing_url":"https://jobs.jobvite.com/other"}`, `{"tenant":true}`, `{"proxy":true}`, `{"render":true}`, `{"ssl_verify":false}`} {
		if _, e := JobviteOptionsFromMetadata("https://jobs.jobvite.com/acme", raw); e == nil {
			t.Fatal("unsupported configuration admitted", raw)
		}
	}
	o, e := JobviteOptionsFromMetadata("https://jobs.jobvite.com/acme/jobs", `{"tenant":"ACME","listing_url":"https://jobs.jobvite.com/careers/acme/jobs"}`)
	if e != nil {
		t.Fatal(e)
	}
	for _, s := range []string{o.Endpoint, "https://jobs.jobvite.com/acme/jobs/positions", o.SearchURL(JobviteSearch{"R&D", 4999})} {
		if !o.ResourceMatches(s) {
			t.Fatal("valid source refused", s)
		}
	}
	for _, s := range []string{"https://jobs.jobvite.com/other/jobs", "https://jobs.jobvite.com/acme/job/ABC123", "https://jobs.jobvite.com/acme/search?c=R%26D&p=5000", "https://jobs.jobvite.com/acme/search?c=R%26D&p=1&token=x", "https://jobs.jobvite.com@other.com/acme/jobs"} {
		if o.ResourceMatches(s) {
			t.Fatal("resource escaped source binding", s)
		}
	}
	c, e := ComeetOptionsFromMetadata("https://example.com/careers", `{"company_id":"C6.001","token":"public fixture"}`)
	if e != nil || !c.API {
		t.Fatal("configured public API refused", e)
	}
	for _, s := range []string{c.Endpoint + "&other=1", "https://www.comeet.co/careers-api/2.0/company/OTHER/positions?token=public+fixture&details=true", "https://example.com/careers"} {
		if c.ResourceMatches(s) {
			t.Fatal("API acquired unrelated request", s)
		}
	}
	for _, raw := range []string{`{"company_id":"..","token":"fixture"}`, `{"company_id":"../other","token":"fixture"}`, `{"company_id":"C6.001","token":"a\r\nb"}`, `{"proxy":true}`, `{"actions":[{}]}`, `{"ssl_verify":false}`} {
		if _, e := ComeetOptionsFromMetadata("https://example.com/careers", raw); e == nil {
			t.Fatal("unsupported configuration admitted", raw)
		}
	}
}
