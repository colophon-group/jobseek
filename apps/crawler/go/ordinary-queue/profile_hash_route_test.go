package queue

import "testing"

func TestHashRoutedAPIProfileBindsCanonicalBoardAndRootResource(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["monitor_needs_browser"] = "api_sniffer", "1"
	c["board_url"] = "https://careers.trip.com/#/jobList"
	c["metadata"] = `{"browser":true,"api_url":"https://careers.trip.com/api/oversea/getOverseaJobAd","method":"POST","json_path":"jobs","url_template":"https://careers.trip.com/#/job-detail?fromId={id}","fields":{"title":"title"}}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != apiSnifferBrowserProfile || p.Endpoint != c["board_url"] || MonitorWorker(p) != Browser {
		t.Fatal("canonical hash board lost browser ownership", err)
	}
	for _, resource := range []string{c["board_url"], "https://careers.trip.com/", "https://careers.trip.com/api/oversea/getOverseaJobAd"} {
		if !APISnifferMonitorResourceMatches(p, c, resource) {
			t.Fatal("bound network resource refused", resource)
		}
	}
	for _, resource := range []string{"https://foreign.example/", "https://careers.trip.com/other", "https://careers.trip.com/api/unproved"} {
		if APISnifferMonitorResourceMatches(p, c, resource) {
			t.Fatal("unqualified resource accepted", resource)
		}
	}
	changed := cloneConfig(c)
	changed["board_url"] = "https://careers.trip.com/#/other"
	if _, err := InspectRichMonitor(profileBoardID, changed); err == nil {
		t.Fatal("unqualified board fragment acquired ownership")
	}
}
