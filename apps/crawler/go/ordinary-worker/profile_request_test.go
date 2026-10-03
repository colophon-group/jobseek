package worker

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestResolvedGreenhouseProfilesFetchActualPythonAPIEndpoints(t *testing.T) {
	body, err := os.ReadFile("../ordinary-queue/testdata/python_greenhouse_profile_tokens.json")
	if err != nil {
		t.Fatal(err)
	}
	var oracle struct {
		Cases []struct {
			Name, Endpoint string
			BoardURL       string `json:"board_url"`
			Metadata       map[string]any
			Accepted       bool
		}
	}
	if json.Unmarshal(body, &oracle) != nil || len(oracle.Cases) != 79 {
		t.Fatal("Python request oracle missing")
	}
	for _, row := range oracle.Cases {
		if !row.Accepted {
			continue
		}
		t.Run(row.Name, func(t *testing.T) {
			metadata, _ := json.Marshal(row.Metadata)
			config := map[string]string{"board_slug": "fixture", "board_url": row.BoardURL, "crawler_type": "greenhouse", "company_id": "00000000-0000-4000-8000-000000000002", "metadata": string(metadata), "check_interval_minutes": "60", "scrape_interval_hours": "24", "throttle_key": "greenhouse", "domain": "greenhouse", "monitor_needs_browser": "0", "scraper_needs_browser": "0"}
			profile, err := queue.InspectGreenhouseMonitor("00000000-0000-4000-8000-000000000001", config)
			if err != nil {
				t.Fatal(err)
			}
			requests := 0
			client := &http.Client{Transport: testRoundTripper(func(req *http.Request) (*http.Response, error) {
				requests++
				if req.URL.String() != row.Endpoint || req.URL.Host != "boards-api.greenhouse.io" || req.Method != http.MethodGet || req.Header.Get("User-Agent") != ordinaryUserAgent || req.Header.Get("Accept") != ordinaryAccept {
					t.Fatal("resolved profile fetched a different resource or changed request defaults")
				}
				return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": []string{"application/json"}}, Body: io.NopCloser(strings.NewReader(`{"jobs":[]}`)), Request: req}, nil
			})}
			if _, err := DiscoverGreenhouse(context.Background(), client, profile.Token); err != nil || requests != 1 {
				t.Fatal("resolved profile did not make its one fixed API request", err)
			}
		})
	}
}
