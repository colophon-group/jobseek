package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestDOMProviderOriginalCompleteAndRejectedInventories(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_dom_provider_contracts.json")
	var cases []struct {
		sharedServiceOriginalCase
		Status string
	}
	if e != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 25 {
		t.Fatal("original provider oracle unavailable")
	}
	complete := []sharedServiceOriginalCase{}
	for _, c := range cases {
		if c.Status == "complete" {
			complete = append(complete, c.sharedServiceOriginalCase)
			continue
		}
		t.Run(c.Name, func(t *testing.T) {
			md, _ := json.Marshal(c.Board.Metadata)
			config := map[string]string{"crawler_type": "dom", "board_url": c.Board.BoardURL, "metadata": string(md), "monitor_needs_browser": "0"}
			calls := 0
			client := verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if "https://"+r.Host+r.URL.String() != c.Board.BoardURL || r.Method != "GET" {
					t.Error("original provider request changed")
				}
				fmt.Fprint(w, c.Response)
			}))
			got, e := discoverDOMInventory(context.Background(), client, queue.GreenhouseMonitorProfile{Provider: "dom", Profile: "dom.direct-rows/v1", Endpoint: c.Board.BoardURL}, config)
			if e == nil || len(got.Jobs) != 0 || calls != len(c.Exchanges) {
				t.Fatal("unproved provider inventory acquired authority", e, calls)
			}
		})
	}
	runServiceAnnotationOriginalCases(t, complete)
}
