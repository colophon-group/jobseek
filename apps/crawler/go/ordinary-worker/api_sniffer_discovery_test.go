package worker

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func configuredAPIFixture(metadata string) (queue.GreenhouseMonitorProfile, map[string]string) {
	c := map[string]string{"crawler_type": "api_sniffer", "monitor_needs_browser": "0", "board_url": "https://example.com/careers", "metadata": metadata}
	o, err := queue.APISnifferMonitorOptions(c)
	if err != nil {
		panic(err)
	}
	return queue.GreenhouseMonitorProfile{Provider: "api_sniffer", Profile: "api_sniffer.http-items/v1", Endpoint: o.Endpoint}, c
}

func TestConfiguredAPIUsesDeclaredPOSTBodyHeadersAndOperationCookies(t *testing.T) {
	p, c := configuredAPIFixture(`{"api_url":"https://example.com/api","method":"POST","post_data":{"search":{"offset":0}},"json_path":"jobs","url_field":"url","fields":{"title":"name","description":"body","skills":"skills"},"pagination":{"param_name":"search.offset","location":"body","style":"offset","start_value":0,"increment":1,"max_pages":2},"request_headers":{"X-Required":"fixture"}}`)
	calls := 0
	client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		calls++
		body, _ := io.ReadAll(r.Body)
		want := `{"search":{"offset":0}}`
		if calls > 1 {
			want = `{"search": {"offset": 1}}`
		}
		if r.Method != "POST" || r.Header.Get("X-Required") != "fixture" || r.Header.Get("Content-Type") != "application/json" || string(body) != want {
			t.Fatal("declared request lost", r.Header, string(body))
		}
		if calls == 2 && r.Header.Get("Cookie") != "session=fixture" {
			t.Fatal("operation cookie lost")
		}
		h := http.Header{}
		if calls == 1 {
			h.Set("Set-Cookie", "session=fixture; Path=/")
		}
		payload := fmt.Sprintf(`{"total":2,"jobs":[{"url":"/jobs/ü-%d%%zz","name":"Engineer","body":"<p>Build</p>","skills":["Go"]}]}`, calls)
		return &http.Response{StatusCode: 200, Header: h, Body: io.NopCloser(strings.NewReader(payload)), Request: r}, nil
	})}
	got, err := discoverAPISnifferInventory(context.Background(), client, p, c)
	if err != nil || calls != 2 || len(got.Jobs) != 2 || got.Truncated || got.Jobs[0].URL != "https://example.com/jobs/ü-1%zz" || got.Jobs[0].Extras["skills"] == nil {
		t.Fatalf("declared API result differs: %+v %v", got, err)
	}
	if client.Jar != nil {
		t.Fatal("operation cookies leaked to process client")
	}
}

func TestConfiguredAPILaterFailuresAndProbeReservationsDiscardInventory(t *testing.T) {
	for _, mode := range []string{"later503", "laterMalformed", "laterReserved", "probeReserved", "failedProbe"} {
		t.Run(mode, func(t *testing.T) {
			endpoint := "https://example.com/api?page=1"
			if strings.Contains(mode, "Probe") || mode == "probeReserved" {
				endpoint += "&size=1"
			}
			p, c := configuredAPIFixture(fmt.Sprintf(`{"api_url":%q,"json_path":"jobs","url_field":"url","fields":{"title":"name"},"transport_attempts":1,"transient_403":true,"pagination":{"param_name":"page","start_value":1,"max_pages":2}}`, endpoint))
			calls := 0
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				h := http.Header{}
				status := 200
				body := `{"total":2,"jobs":[{"url":"/jobs/1","name":"Engineer"}]}`
				if calls > 1 {
					switch mode {
					case "later503", "failedProbe":
						status = 503
					case "laterMalformed":
						body = "invalid"
					default:
						h.Set("TDM-Reservation", "1")
						status = 503
					}
				}
				return &http.Response{StatusCode: status, Header: h, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})}
			got, err := discoverAPISnifferInventory(context.Background(), client, p, c)
			if err == nil || len(got.Jobs) != 0 || got.Response == nil {
				t.Fatal("failed inventory became success", got, err)
			}
			if strings.Contains(mode, "Reserved") && (!got.Response.reserved || calls != 2) {
				t.Fatal("publisher probe/later-page signal swallowed", got.Response, calls)
			}
		})
	}
}
