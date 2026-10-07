package queue

import (
	"strings"
	"testing"
)

func TestRSSPaginationAndRenderedProfilesBindOriginalOptions(t *testing.T) {
	for _, preset := range []string{"generic", "wp_job_manager"} {
		for _, render := range []bool{false, true} {
			for _, summary := range []bool{false, true} {
				if summary && preset != "generic" {
					continue
				}
				t.Run(preset+"/"+strings.Join([]string{boolLabel(render), boolLabel(summary)}, "/"), func(t *testing.T) {
					c := profileConfig()
					c["crawler_type"] = "rss"
					md := `{"preset":"` + preset + `","feed_url":"https://example.com/feed?feed=job_feed","scraper_type":"skip"`
					name := "rss."
					if render {
						name += "rendered-"
						md += `,"render":true,"wait":"domcontentloaded","timeout":30000`
						c["monitor_needs_browser"] = "1"
					}
					name += preset
					if summary {
						name = "rss."
						if render {
							name += "rendered-"
						}
						name += "generic-summary"
						md += `,"description_mode":"title_employment_location"`
					}
					if preset == "generic" {
						md += `,"pagination":{"param_name":"page","start":2,"increment":2,"page_size":20,"max_pages":3}`
					}
					md += `}`
					c["metadata"] = md
					p, e := InspectRichMonitor(profileBoardID, c)
					if e != nil || p.Profile != name+"-skip/v1" || p.RSSPagination == nil {
						t.Fatal(p, e)
					}
					if (MonitorWorker(p) == Browser) != render {
						t.Fatal("wrong worker class")
					}
					page := 2
					param := "page"
					if preset == "wp_job_manager" {
						page = 1
						param = "paged"
					}
					endpoint := p.Endpoint + "&" + param + "=" + map[int]string{1: "1", 2: "2"}[page]
					if !RSSMonitorResourceMatches(p, c, endpoint) || !initialMonitorResourceMatches(p, endpoint) || RSSMonitorResourceMatches(p, c, endpoint+"&other=1") || RSSMonitorResourceMatches(p, c, strings.Replace(endpoint, "example.com", "other.example", 1)) {
						t.Fatal("page resource binding differs")
					}
					changed := cloneConfig(c)
					changed["metadata"] = strings.Replace(md, `"page_size":20`, `"page_size":10`, 1)
					if preset == "generic" {
						other, e := InspectRichMonitor(profileBoardID, changed)
						if e != nil || other.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
							t.Fatal("pagination not bound", e)
						}
					}
				})
			}
		}
	}
}
func boolLabel(v bool) string {
	if v {
		return "yes"
	}
	return "no"
}
