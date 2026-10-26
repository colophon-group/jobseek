package worker

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func tupuTestCard(id int) string {
	return fmt.Sprintf(`<div class="position-item" pid="%024x" recruitment="SOCIALRECRUITMENT"><div class="position-name"><span class="title"><span class="txt"> 工程师 </span></span></div><div class="e-city"><span class="txt">工作城市： 上海 </span></div><span class="time">发布于：2026-10-01</span><div class="e-salary"><span class="txt">薪资待遇：10000-20000</span></div></div>`, id)
}
func tupuTestPage(total int, ids ...int) string {
	var s strings.Builder
	fmt.Fprintf(&s, `<div id="position-list" positioncount="%d">`, total)
	for _, id := range ids {
		s.WriteString(tupuTestCard(id))
	}
	s.WriteString("</div>")
	return s.String()
}
func tupuTestConfig() map[string]string {
	return map[string]string{"crawler_type": "dom", "board_url": "https://fixture.tupu360.com/fixture/position/index", "monitor_needs_browser": "0", "metadata": `{"tupu360_listing":true,"scraper_type":"tupu360","scraper_config":{"enrich":["description"]}}`}
}

func TestTupu360CompleteAndFailedLaterPages(t *testing.T) {
	for _, mode := range []string{"complete", "changed-total", "duplicate", "early-empty", "late-malformed", "late-404", "reserved", "retry", "zero"} {
		t.Run(mode, func(t *testing.T) {
			c := tupuTestConfig()
			p := queue.GreenhouseMonitorProfile{Provider: "dom", Profile: "dom.direct-rows/v1", Endpoint: c["board_url"]}
			calls := 0
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				offset := r.URL.Query().Get("offset")
				if r.Method != "GET" || r.Host != "fixture.tupu360.com" || r.URL.Path != "/fixture/position/index" || r.URL.Query().Get("max") != "100" || len(r.URL.Query()) != 2 || offset != "0" && offset != "1" {
					t.Error("request left unfiltered provider pagination")
				}
				body, status, headers := tupuTestPage(2, 1), 200, http.Header{}
				if mode == "zero" {
					body = tupuTestPage(0)
				} else if mode == "retry" && calls == 1 {
					status = 503
				} else if offset == "1" {
					body = tupuTestPage(2, 2)
					switch mode {
					case "changed-total":
						body = tupuTestPage(3, 2)
					case "duplicate":
						body = tupuTestPage(2, 1)
					case "early-empty":
						body = tupuTestPage(2)
					case "late-malformed":
						body = "<html>layout drift</html>"
					case "late-404":
						status = 404
					case "reserved":
						status = 503
						headers.Set("TDM-Reservation", "1")
					}
				}
				return &http.Response{StatusCode: status, Header: headers, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})}
			got, err := discoverTupu360Inventory(context.Background(), client, p, c, func(context.Context, time.Duration) error { return nil })
			if mode == "complete" || mode == "retry" {
				if err != nil || got.Truncated || len(got.Jobs) != 2 || got.Jobs[0].Title == nil || *got.Jobs[0].Title != "工程师" || got.Jobs[0].Language != "zh" || got.Jobs[0].Locations[0] != "上海" || got.Jobs[0].DatePosted != "2026-10-01" {
					t.Fatal("complete rich inventory changed", err)
				}
			} else if mode == "zero" {
				if err != nil || len(got.Jobs) != 0 || calls != 1 {
					t.Fatal("explicit zero changed", err)
				}
			} else {
				if err == nil || len(got.Jobs) != 0 {
					t.Fatal("failed later page retained partial inventory")
				}
				if mode == "reserved" && (got.Response == nil || !got.Response.reserved) {
					t.Fatal("later publisher reservation lost")
				}
			}
		})
	}
}
