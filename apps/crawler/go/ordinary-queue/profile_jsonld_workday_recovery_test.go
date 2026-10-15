package queue

import (
	"strings"
	"testing"
)

func TestJSONLDRecoveryBindsTenantAndSeparateProxyNeed(t *testing.T) {
	c := jsonldDetailConfig()
	c["metadata"] = `{"scraper_type":"json-ld","scraper_config":{"workday_fallback":{"company":"fixture","wd_instance":"wd5","site":"External","proxy":true}}}`
	p, err := InspectJSONLDDetail(jsonldBoardID, c, "https://careers.example.com/job/REQ100/engineer", Simple)
	if err != nil || p.JSONLDRecovery == nil || !p.JSONLDRecovery.Proxy || p.JSONLDRecovery.Company != "fixture" || ProfileRequiresProxy(p.Profile) {
		t.Fatal("primary/recovery transport binding lost", err)
	}
	a := &Authority{ownership: &OwnershipPlan{document: ownershipDocument{Details: []ownershipDetail{{BoardID: jsonldBoardID, Profile: p.Profile, Config: c}}}}}
	if !a.RequiresProxyHTTP() {
		t.Fatal("fallback proxy resources not required")
	}
	before := p.EffectiveBoardSHA256
	c["metadata"] = strings.Replace(c["metadata"], `"proxy":true`, `"proxy":false`, 1)
	p, err = InspectJSONLDDetail(jsonldBoardID, c, p.SourceURL, Simple)
	if err != nil || p.EffectiveBoardSHA256 == before || p.JSONLDRecovery.Proxy {
		t.Fatal("recovery mask/config identity lost", err)
	}
	for _, value := range []string{`{"company":"bad.host","wd_instance":"wd5","site":"External","proxy":true}`, `{"company":"fixture","wd_instance":"wd5","site":"External"}`} {
		c["metadata"] = `{"scraper_type":"json-ld","scraper_config":{"workday_fallback":` + value + `}}`
		if _, err := InspectJSONLDDetail(jsonldBoardID, c, "https://careers.example.com/job/REQ100", Simple); err == nil {
			t.Fatal("invalid recovery admitted")
		}
	}
}

func TestJSONLDRecoveryDetachedProfileAndOrderedProxyNeed(t *testing.T) {
	c := jsonldDetailConfig()
	c["metadata"] = `{"scraper_type":"json-ld","scraper_config":{"workday_fallback":{"company":"fixture","wd_instance":"wd5","site":"External","proxy":true},"fallback":{"type":"dom","config":{"steps":[{"tag":"h1","field":"title"}]}}}}`
	p, err := InspectOrderedDetail(jsonldBoardID, c, "https://careers.example.com/job/REQ100/engineer", Simple)
	if err != nil || len(p.Stages) != 2 || p.Stages[0].JSONLDRecovery == nil {
		t.Fatal("ordered configured recovery lost", err)
	}
	a := &Authority{ownership: &OwnershipPlan{document: ownershipDocument{Details: []ownershipDetail{{BoardID: jsonldBoardID, Profile: orderedHTTPDetailProfile, Config: c}}}}}
	if !a.RequiresProxyHTTP() || ProfileRequiresProxy(p.Stages[0].Profile) {
		t.Fatal("ordered fallback proxy resources lost")
	}
	d := &CurrentWorkdayDetail{profile: p.Stages[0]}
	detached := d.Profile()
	detached.JSONLDRecovery.Company = "unbound"
	if d.Profile().JSONLDRecovery.Company != "fixture" {
		t.Fatal("detached recovery changed the immutable attempt")
	}
}
