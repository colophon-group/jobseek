package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestDOMProviderOriginalCompleteAndRejectedInventories(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_dom_provider_contracts.json")
	var cases []struct {
		sharedServiceOriginalCase
		Status string
	}
	if e != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 26 {
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

// Public evidence stays private; the committed oracle remains the CI input.
func TestDOMProviderSameCapturedPublicOutputs(t *testing.T) {
	directory := os.Getenv("JOBSEEK_DOM_PROVIDER_PUBLIC_CAPTURE_DIR")
	if directory == "" {
		t.Skip("requires original private public inventories")
	}
	cases := []sharedServiceOriginalCase{}
	for _, slug := range []string{"swiss-medical-network-spital-zofingen", "bdo-brazil", "cencora-profarma-lg", "implenia-apprenticeships-ch", "finma-careers", "ge-healthcare-icometrix", "ammann-abg", "world-aquatics-lucca", "unaids-consulting", "international-skating-union-lucca"} {
		raw, err := os.ReadFile(filepath.Join(directory, "native-dom-provider-"+slug+"-original-public-capture1-2026-10-10.json"))
		var source struct {
			Status string
			Board  struct {
				BoardURL string `json:"board_url"`
				Metadata map[string]any
			}
			Jobs      []map[string]any
			Exchanges []struct {
				Method, URL, Body string
				RequestBody       string `json:"request_body"`
				Status            int
				Headers           map[string]string `json:"response_headers"`
			}
		}
		if err != nil || json.Unmarshal(raw, &source) != nil || source.Status != "complete" {
			t.Fatal("original complete inventory unavailable", slug)
		}
		c := sharedServiceOriginalCase{Name: slug, Expected: source.Jobs}
		c.Board.Provider, c.Board.BoardURL, c.Board.Metadata = "dom", source.Board.BoardURL, source.Board.Metadata
		for _, x := range source.Exchanges {
			entry := sharedServiceOriginalCase{}.Exchanges
			entry = append(entry, struct {
				Method, URL, Body string
				Response          struct {
					Status  int
					Body    string
					Headers map[string]string
				}
			}{Method: x.Method, URL: x.URL, Body: x.RequestBody})
			entry[0].Response.Status, entry[0].Response.Body, entry[0].Response.Headers = x.Status, x.Body, x.Headers
			c.Exchanges = append(c.Exchanges, entry[0])
		}
		cases = append(cases, c)
	}
	runServiceAnnotationOriginalCases(t, cases)
}
