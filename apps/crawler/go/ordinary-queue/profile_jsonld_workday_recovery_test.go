package queue

import (
	"encoding/json"
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
	a := &Authority{ownership: &OwnershipPlan{document: ownershipDocument{Details: []ownershipDetail{{BoardID: jsonldBoardID, Worker: Simple, Profile: p.Profile, Config: c}}}}}
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
	a := &Authority{ownership: &OwnershipPlan{document: ownershipDocument{Details: []ownershipDetail{{BoardID: jsonldBoardID, Worker: Simple, Profile: orderedHTTPDetailProfile, Config: c}}}}}
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

func TestJSONLDRecoveryOrderedProxyNeedBindsWorker(t *testing.T) {
	for _, lane := range []WorkerType{Simple, Browser} {
		for _, proxy := range []bool{false, true} {
			t.Run(string(lane)+map[bool]string{false: "-direct", true: "-proxy"}[proxy], func(t *testing.T) {
				c := jsonldDetailConfig()
				fallback := map[string]any{"steps": []any{map[string]any{"tag": "h1", "field": "title"}}}
				if lane == Browser {
					c["scraper_needs_browser"] = "1"
					fallback["render"] = true
				}
				body, err := json.Marshal(map[string]any{"scraper_type": "json-ld", "scraper_config": map[string]any{"workday_fallback": map[string]any{"company": "fixture", "wd_instance": "wd5", "site": "External", "proxy": proxy}, "fallback": map[string]any{"type": "dom", "config": fallback}}})
				if err != nil {
					t.Fatal(err)
				}
				c["metadata"] = string(body)
				p, err := InspectOrderedDetail(jsonldBoardID, c, c["board_url"], lane)
				if err != nil {
					t.Fatal("ordered reference unsupported", err)
				}
				binding := ownershipDetail{BoardID: jsonldBoardID, Worker: lane, Profile: orderedDetailOwnership(p).Profile, Config: c}
				a := &Authority{ownership: &OwnershipPlan{document: ownershipDocument{Details: []ownershipDetail{binding}}}}
				if a.RequiresProxyHTTP() != proxy {
					t.Fatal("ordered stage recovery proxy requirement lost")
				}
			})
		}
	}
}
