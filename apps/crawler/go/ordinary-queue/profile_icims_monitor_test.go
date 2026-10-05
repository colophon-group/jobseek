package queue

import (
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"testing"
)

func icimsMonitorConfig() map[string]string {
	c := profileConfig()
	c["crawler_type"] = "icims"
	c["domain"] = "careers-native.icims.com"
	c["throttle_key"] = c["domain"]
	c["board_url"] = "https://careers-native.icims.com/jobs/search?ss=1"
	c["metadata"] = `{"scraper_type":"json-ld","cross_locale_dedupe":{"peer_host":"careers-peer.icims.com","title_aliases":{"Engineer":"Ingenieur"}}}`
	return c
}
func TestICIMSMonitorAdmissionBindingsAndResources(t *testing.T) {
	c := icimsMonitorConfig()
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "icims.listing-urls/v1" || p.Endpoint != dom.ICIMSListingURL("careers-native.icims.com", 0) {
		t.Fatal(p, err)
	}
	c["metadata"] = `{"cross_locale_dedupe":{"title_aliases":{"Engineer":"Ingenieur"},"peer_host":"careers-peer.icims.com"},"scraper_type":"json-ld"}`
	q, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || q.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 {
		t.Fatal("semantic alias configuration changed binding", err)
	}
	for _, source := range []string{p.Endpoint, dom.ICIMSListingURL("careers-native.icims.com", 999), dom.ICIMSListingURL("careers-peer.icims.com", 1)} {
		if !ICIMSMonitorResourceMatches(p, c, source) {
			t.Fatal("configured resource rejected", source)
		}
	}
	for _, source := range []string{p.Endpoint + "&extra=1", dom.ICIMSListingURL("untrusted.icims.com", 0), dom.ICIMSListingURL("careers-peer.icims.com", 1000), "https://careers-native.icims.com/jobs/1/job?in_iframe=1"} {
		if ICIMSMonitorResourceMatches(p, c, source) {
			t.Fatal("unbound evidence accepted", source)
		}
	}
	if !ICIMSMonitorPrimaryGone(c, p.Endpoint, 410) || ICIMSMonitorPrimaryGone(c, dom.ICIMSListingURL("careers-peer.icims.com", 0), 404) {
		t.Fatal("peer became primary gone")
	}
	c["metadata"] = `{"scraper_type":"json-ld","jibe_url":"https://careers.example.com/jobs","jibe_job_hosts":["careers-native.icims.com"]}`
	q, err = InspectRichMonitor(profileBoardID, c)
	if err != nil {
		t.Fatal(err)
	}
	if !ICIMSMonitorResourceMatches(q, c, "https://careers.example.com/api/jobs?page=1&limit=100") || ICIMSMonitorPrimaryGone(c, q.Endpoint, 404) || ICIMSMonitorResourceMatches(q, c, "https://careers.example.com/api/jobs?page=1001&limit=100") {
		t.Fatal("Jibe resource/gone bound changed")
	}
	for _, md := range []string{`{"unknown":true}`, `{"host":"careers-native.icims.com","host":"evil.icims.com"}`, `{"cross_locale_dedupe":{"peer_host":"careers-peer.icims.com","title_aliases":{"Engineer":"one","Engineer":"two"}}}`, `{"job_hosts":["evil.example.com"]}`, `{"jibe_url":"https://u:p@careers.example.com/jobs","jibe_job_hosts":["careers-native.icims.com"]}`, `{"delist_threshold":2147483648}`} {
		c["metadata"] = md
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unsafe configuration admitted", md)
		}
	}
	c = icimsMonitorConfig()
	c["monitor_needs_browser"] = "1"
	if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
		t.Fatal("browser route admitted")
	}
}
