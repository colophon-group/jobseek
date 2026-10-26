package queue

import "testing"

func TestRenderedMyJobShopAnnotationKeepsOwnershipBinding(t *testing.T) {
	c := domMonitorConfig()
	c["monitor_needs_browser"] = "1"
	c["metadata"] = `{"render":true,"my_job_shop":true,"link_selector":"a.job","empty_states":[{"selector":"p.empty","exact_text":"No jobs"}],"scraper_type":"json-ld"}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || MonitorWorker(p) != Browser {
		t.Fatal("original rendered annotation rejected", err)
	}
	listing, _, err := RenderedDOMMonitorOptions(c)
	if err != nil || !listing.MyJobShop || listing.Selector != "a.job" {
		t.Fatal("held listing/empty proof lost", err)
	}
	c["metadata"] = `{"render":true,"link_selector":"a.job","empty_states":[{"selector":"p.empty","exact_text":"No jobs"}],"scraper_type":"json-ld"}`
	other, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || other.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("annotation removed from original ownership hash", err)
	}
	c["metadata"] = `{"render":true,"my_job_shop":"true","link_selector":"a.job","scraper_type":"json-ld"}`
	if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
		t.Fatal("nonboolean annotation admitted")
	}
}
