package queue

import "testing"

func TestBeisenProfileBindsOptionalIdentityAndEveryPublicResource(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["board_url"], c["metadata"] = "beisen", "https://fixture.zhiye.com/", `{"scraper_type":"skip"}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "beisen.portal-items/v1" || MonitorWorker(p) != Simple {
		t.Fatal(err)
	}
	for _, resource := range []string{p.Endpoint, p.Endpoint + "api/Jobad/GetJobAdPageList", p.Endpoint + "Social", p.Endpoint + "index", p.Endpoint + "Social?PageIndex=2"} {
		if !BeisenMonitorResourceMatches(p, c, resource) {
			t.Fatal("lost declared resource", resource)
		}
	}
	for _, resource := range []string{"https://other.zhiye.com/", p.Endpoint + "Social?PageIndex=1", p.Endpoint + "Social?PageIndex=02", p.Endpoint + "Social?PageIndex=50001", p.Endpoint + "Social?PageIndex=2&foreign=1"} {
		if BeisenMonitorResourceMatches(p, c, resource) {
			t.Fatal("unbound resource accepted")
		}
	}
	if !BeisenMonitorPrimaryGone(c, p.Endpoint, 404, false) || !BeisenMonitorPrimaryGone(c, p.Endpoint+"Social", 410, false) || !BeisenMonitorPrimaryGone(c, p.Endpoint, 200, true) || BeisenMonitorPrimaryGone(c, p.Endpoint+"api/Jobad/GetJobAdPageList", 404, false) || BeisenMonitorPrimaryGone(c, p.Endpoint+"Social?PageIndex=2", 404, false) || BeisenMonitorPrimaryGone(c, p.Endpoint, 200, false) {
		t.Fatal("gone authority changed")
	}
	c["metadata"] = `{"tenant":"fixture","variant":"legacy","listing_path":"/Social","legacy_template":"standard","scraper_type":"dom","scraper_config":{"enrich":["description"],"steps":[{"tag":"h1","field":"description"}]}}`
	other, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || other.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("variant/description binding lost", err)
	}
	for _, md := range []string{`{"unknown":true}`, `{"render":true}`, `{"proxy":true}`, `{"tenant":"other","variant":"legacy","listing_path":"/Social","legacy_template":"standard"}`} {
		c["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unsupported option acquired authority", md)
		}
	}
}
