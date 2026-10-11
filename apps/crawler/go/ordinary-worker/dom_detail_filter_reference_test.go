package worker

import (
	"context"
	"encoding/json"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
)

func TestDOMInactiveDetailFiltersMatchOriginalReference(t *testing.T) {
	var fixture struct {
		Source string `json:"source_revision"`
		Cases  []struct {
			Name, Body         string
			Mode, Selector     string
			States             any
			Status             int
			Valid, Keep, Error bool
		}
	}
	body, e := os.ReadFile("../dom-detail/testdata/python_inactive_detail_filters.json")
	if e != nil || json.Unmarshal(body, &fixture) != nil || fixture.Source != "f20f5fe83b0ba1a700bbb1d3566b5fc25b5e5b77" || len(fixture.Cases) != 27 {
		t.Fatal("original reference unavailable")
	}
	for _, x := range fixture.Cases {
		t.Run(x.Name, func(t *testing.T) {
			options := dom.Object{"link_selector": "a.job", "inactive_detail_states": x.States}
			if x.Mode == "exclude" {
				delete(options, "inactive_detail_states")
				options["exclude_detail_selector"] = x.Selector
			}
			o, e := dom.ListingOptions(options, "https://example.com/careers")
			if (e == nil) != x.Valid {
				t.Fatal("original configuration outcome changed", e)
			}
			if !x.Valid || strings.HasPrefix(x.Name, "config-") {
				return
			}
			md, _ := json.Marshal(options)
			c := map[string]string{"crawler_type": "dom", "board_url": "https://example.com/careers", "metadata": string(md)}
			p := queue.GreenhouseMonitorProfile{Provider: "dom", Endpoint: c["board_url"]}
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				return &http.Response{StatusCode: x.Status, Header: http.Header{"Content-Type": []string{"text/html"}}, Body: io.NopCloser(strings.NewReader(x.Body)), Request: r}, nil
			})}
			keep, _, e := fetchDOMVerificationClassified(context.Background(), client, p, c, "https://example.com/jobs/1", noSecondaryWait, func(s string) (bool, error) { return dom.DetailSelectorKeeps(s, o, x.Mode != "exclude") }, true)
			if (e != nil) != x.Error || keep != x.Keep {
				t.Fatal("original exact selector classification changed", keep, e)
			}
		})
	}
}
