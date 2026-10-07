package apisniffer

import (
	"context"
	"fmt"
	"net/url"
	"reflect"
	"strings"
	"testing"
)

func TestRecruiterboxCanonicalScopeAndIncompleteListing(t *testing.T) {
	o, e := RecruiterboxOptionsFromMetadata("https://TENANT.recruiterbox.com/jobs/ABC?source=feed", `{}`)
	if e != nil || o.Tenant != "tenant" || o.PageURL(1) != "https://tenant.hire.trakstar.com/?limit=100&p=1" {
		t.Fatal(o, e)
	}
	for _, source := range []string{"http://tenant.recruiterbox.com/", "https://www.recruiterbox.com/", "https://tenant.recruiterbox.com/?p=1&p=2", "https://tenant.recruiterbox.com/?limit=0", "https://tenant.recruiterbox.com/jobs/abc#x", "https://tenant.recruiterbox.com/jobs/abc?source=", "https://user@tenant.recruiterbox.com/", "https://tenant.recruiterbox.com/jobs/%61bc", "https://tenant.recruiterbox.com:444/"} {
		if _, e := RecruiterboxOptionsFromMetadata(source, `{}`); e == nil {
			t.Fatal("unsafe source admitted", source)
		}
	}
	if !o.ResourceMatches(o.PageURL(500)) || o.ResourceMatches(o.PageURL(501)) || o.ResourceMatches("https://other.hire.trakstar.com/?limit=100&p=1") {
		t.Fatal("request scope differs")
	}
	join := func(base, href string) (string, error) {
		b, e := url.Parse(base)
		if e != nil {
			return "", e
		}
		u, e := url.Parse(href)
		if e != nil {
			return "", e
		}
		return b.ResolveReference(u).String(), nil
	}
	body := `<script>"total_jobs": "1"</script><a href="https://tenant.recruiterbox.com/jobs/ABC?source=a">one</a><a href="/jobs/abc/">duplicate</a><a href="/jobs/xyz/#fragment">invalid</a><a href="https://other.hire.trakstar.com/jobs/xyz/">foreign</a>`
	p, e := ParseRecruiterboxPage(body, o, 1, join)
	if e != nil || !p.Complete || !reflect.DeepEqual(p.URLs, []string{"https://tenant.hire.trakstar.com/jobs/abc/"}) {
		t.Fatal(p, e)
	}
	p, e = ParseRecruiterboxPage(body+strings.Repeat("x", 2000001), o, 1, join)
	if e != nil || p.Complete {
		t.Fatal("oversized listing became complete", p, e)
	}
	if !RecruiterboxInactive("Recruiterbox.com/inactive-ats INACTIVE ACCOUNT no longer using Trakstar Hire") || RecruiterboxInactive("inactive account") {
		t.Fatal("unrelated shell became tombstone")
	}
}

func TestJobCloudAliasesPortalsAndExactPageMetadata(t *testing.T) {
	uuid := "ABCDEF01-2345-6789-ABCD-EF0123456789"
	o, e := JobCloudOptionsFromMetadata("https://www.jobup.ch/fr/societes/"+uuid+"-tenant/", `{"document_company_id":123}`)
	if e != nil || o.CompanyID != strings.ToLower(uuid) || o.DocumentCompanyID != "123" || o.Portal != "jobup" || o.DetailPath() != "emplois" {
		t.Fatal(o, e)
	}
	for _, md := range []string{`{"company_id":"123","document_company_id":"456"}`, `{"document_company_id":"00000000-0000-0000-0000-000000000000"}`, `{"document_company_id":null}`, `{"portal":"jobup","locale":"de"}`, `{"proxy":true}`} {
		if _, e := JobCloudOptionsFromMetadata("https://www.jobup.ch/fr/societes/"+uuid+"-tenant/", md); e == nil {
			t.Fatal("invalid alias/transport admitted", md)
		}
	}
	if !o.ResourceMatches(o.SearchURL(2)) || o.ResourceMatches(o.SearchURL(501)) || o.ResourceMatches(strings.Replace(o.SearchURL(1), "companyIds=", "companyIds=foreign", 1)) {
		t.Fatal("company request scope differs")
	}
	for _, body := range []string{
		`{"documents":[],"numPages":0,"totalHits":0,"currentPage":true,"rows":100,"start":0}`,
		`{"documents":[],"numPages":0.0,"totalHits":0,"currentPage":1,"rows":100,"start":0}`,
		`{"documents":[],"numPages":501,"totalHits":0,"currentPage":1,"rows":100,"start":0}`,
		`{"documents":[],"numPages":0,"totalHits":0,"currentPage":1,"rows":100,"start":1}`,
	} {
		d, e := Decode([]byte(body))
		if e != nil {
			t.Fatal(e)
		}
		if _, e := ParseJobCloudPage(d, 1); e == nil {
			t.Fatal("invalid page metadata admitted", body)
		}
	}
	// Python equality accepts floating rows and false for a zero start, while
	// numPages/currentPage/totalHits require integers exactly.
	d, _ := Decode([]byte(`{"documents":[],"numPages":0,"totalHits":0,"currentPage":1,"rows":100.0,"start":false}`))
	if _, e := ParseJobCloudPage(d, 1); e != nil {
		t.Fatal(e)
	}
	d, _ = Decode([]byte(fmt.Sprintf(`{"documents":[{"id":%q,"company":{"id":123}}],"numPages":1,"totalHits":1,"currentPage":1,"rows":100,"start":0}`, uuid)))
	urls, e := DiscoverJobCloud(context.Background(), o, func(context.Context, string) (*Document, error) { return d, nil })
	if e != nil || !reflect.DeepEqual(urls, []string{"https://www.jobup.ch/fr/emplois/detail/" + strings.ToLower(uuid) + "/"}) {
		t.Fatal(urls, e)
	}
}
