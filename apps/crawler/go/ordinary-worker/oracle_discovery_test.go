package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func oracleFixture() (queue.GreenhouseMonitorProfile, map[string]string) {
	c := map[string]string{"crawler_type": "oracle_hcm", "monitor_needs_browser": "0", "board_url": "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs", "metadata": `{}`}
	o, err := queue.OracleMonitorOptions(c)
	if err != nil {
		panic(err)
	}
	return queue.GreenhouseMonitorProfile{Provider: "oracle_hcm", Profile: "oracle_hcm.finder-items/v1", Endpoint: o.Endpoint()}, c
}

func TestOracleMonitorRetriesIdenticalFinderAndPreservesRichFields(t *testing.T) {
	p, c := oracleFixture()
	calls := 0
	client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.URL.String() != p.Endpoint || r.Method != "GET" {
			t.Fatal("finder request changed", r.URL)
		}
		status, body := 302, ""
		if calls == 2 {
			status, body = 200, `{"items":[{"TotalJobsCount":1,"requisitionList":[{"Id":300000013747000,"Title":"Engineer","PrimaryLocation":"Zurich","PostedDate":"2026-10-05","JobSchedule":"Full time"}]}]}`
		}
		return &http.Response{StatusCode: status, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
	})}
	got, err := discoverOracleInventory(context.Background(), client, p, c)
	if err != nil || calls != 2 || got.Truncated || len(got.Jobs) != 1 || got.Jobs[0].Title == nil || *got.Jobs[0].Title != "Engineer" || len(got.Jobs[0].Locations) != 1 || got.Jobs[0].Locations[0] != "Zurich" || got.Jobs[0].DatePosted != "2026-10-05" || got.Jobs[0].EmploymentType != "Full time" || !strings.HasSuffix(got.Jobs[0].URL, "/job/300000013747000") {
		t.Fatal("Oracle rich inventory differs", got, err, calls)
	}
	if client.Jar != nil {
		t.Fatal("operation cookies leaked")
	}
}

func TestOracleLaterFailureAndPublisherHeaderNeverCompletePartialInventory(t *testing.T) {
	for _, reserved := range []bool{false, true} {
		t.Run(fmt.Sprint(reserved), func(t *testing.T) {
			p, c := oracleFixture()
			rows := make([]map[string]any, 200)
			for n := range rows {
				rows[n] = map[string]any{"Id": n + 1, "Title": "Engineer"}
			}
			data, _ := json.Marshal(map[string]any{"items": []any{map[string]any{"TotalJobsCount": 201, "requisitionList": rows}}})
			calls := 0
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				status, body := 200, string(data)
				h := http.Header{}
				if calls == 2 {
					if !strings.HasSuffix(r.URL.String(), ",offset=200") {
						t.Fatal("incorrect pagination")
					}
					status, body = 404, ""
					if reserved {
						status = 503
						h.Set("TDM-Reservation", "1")
						h.Set("TDM-Policy", "https://fixture.fa.em2.oraclecloud.com/policy")
					}
				}
				return &http.Response{StatusCode: status, Header: h, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})}
			got, err := discoverOracleInventory(context.Background(), client, p, c)
			if err == nil || calls != 2 || len(got.Jobs) != 0 || got.Response == nil || got.Response.reserved != reserved {
				t.Fatal("failed/opted-out inventory accepted", got, err, calls)
			}
		})
	}
}

func TestOracleRetryCancellationRetainsClaimContext(t *testing.T) {
	p, c := oracleFixture()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	calls := 0
	client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		calls++
		cancel()
		return &http.Response{StatusCode: 429, Header: http.Header{}, Body: io.NopCloser(strings.NewReader("")), Request: r}, nil
	})}
	got, err := discoverOracleInventory(ctx, client, p, c)
	if err != context.Canceled || calls != 1 || len(got.Jobs) != 0 {
		t.Fatal("retry escaped cancellation", got, err, calls)
	}
}
