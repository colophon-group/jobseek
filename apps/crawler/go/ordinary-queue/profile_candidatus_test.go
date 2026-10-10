package queue

import "testing"

func TestCandidatusOriginalQueueScopeAndConfigurationBinding(t *testing.T) {
	c := profileConfig()
	c["crawler_type"] = "candidatus"
	c["board_url"] = "https://carrieres.candidatus.com/site-emploi,ZmFrZQ"
	c["monitor_needs_browser"] = "1"
	c["metadata"] = `{"scraper_type":"json-ld"}`
	p, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || p.Profile != CandidatusHTTPProfile || MonitorWorker(p) != Browser || ProfileRequiresProxy(p.Profile) {
		t.Fatal("original queue or transport changed", e)
	}
	options, e := CandidatusMonitorOptions(c)
	if e != nil || options.MaxJobs != 1000 {
		t.Fatal("original job cap changed", e)
	}
	post := c["board_url"] + "?id=ZmFrZQ&lang=&intra=&v=&rub="
	if !NativeBrowserMonitorResourceMatches(p, c, post) || !apiNativeBrowserProfileResourceMatches(p, post) {
		t.Fatal("published form resource not bound")
	}
	for _, bad := range []string{"https://other.example/site-emploi,ZmFrZQ", post + "&next=private", c["board_url"] + "?id=OTHER&lang=&intra=&v=&rub=", c["board_url"] + "?id=ZmFrZQ&id=ZmFrZQ&lang=&intra=&v=&rub="} {
		if options.ResourceMatches(bad) {
			t.Error("foreign/ambiguous form resource accepted", bad)
		}
	}
	c["metadata"] = `{"scraper_type":"json-ld","max_jobs":500}`
	changed, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || changed.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("job cap escaped immutable binding", e)
	}
	for _, md := range []string{`{"proxy":true}`, `{"max_jobs":0}`, `{"max_jobs":1001}`, `{"timeout":60000}`, `{"wait":"networkidle"}`, `{"ssl_verify":false}`, `{"unknown":true}`} {
		c["metadata"] = md
		if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
			t.Error("unqualified transport/config admitted", md)
		}
	}
	for _, bad := range []string{"http://carrieres.candidatus.com/annonce-emploi,ABC", "https://private.example/annonce-emploi,ABC", "https://carrieres.candidatus.com/other,ABC"} {
		if _, e := CandidatusCanonicalDetailURL(bad); e == nil {
			t.Error("foreign detail identity accepted", bad)
		}
	}
	if got, e := CandidatusCanonicalDetailURL("https://carrieres.candidatus.com:443/annonce-emploi,ABC?temporary=1#top"); e != nil || got != "https://carrieres.candidatus.com/annonce-emploi,ABC" {
		t.Fatal("original canonical identity changed", e)
	}
}
