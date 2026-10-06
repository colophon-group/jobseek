package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type secondaryHTTPCase struct {
	Provider, Name, Endpoint string
	BoardURL                 string `json:"board_url"`
	Metadata                 json.RawMessage
	Pages                    map[string]json.RawMessage
	Requests                 []secondaryHTTPRequest
	Expected                 struct {
		Error, Gone, Truncated bool
		Jobs                   []map[string]any
		URLs                   []string
	}
}
type secondaryHTTPRequest struct {
	Method, URL, Prefix string
	Body                any
}

func secondaryHTTPCases(t *testing.T) []secondaryHTTPCase {
	t.Helper()
	raw, e := os.ReadFile("testdata/python_secondary_provider_http.json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct{ HTTP []secondaryHTTPCase }
	if json.Unmarshal(raw, &corpus) != nil || len(corpus.HTTP) != 25 {
		t.Fatal("actual Python HTTP reference missing")
	}
	return corpus.HTTP
}
func secondaryHTTPFixture(t *testing.T, c secondaryHTTPCase, requests *[]secondaryHTTPRequest, mutex *sync.Mutex) *VerifiedDirectHTTP {
	t.Helper()
	counts := map[string]int{}
	return verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := "https://" + r.Host + r.URL.String()
		if c.Provider == "softgarden" {
			key = strings.TrimSuffix(key, "/")
		}
		body, e := io.ReadAll(r.Body)
		if e != nil {
			t.Error(e)
			w.WriteHeader(500)
			return
		}
		var decoded any
		if len(body) > 0 && json.Unmarshal(body, &decoded) != nil {
			t.Error("bad request JSON")
			w.WriteHeader(500)
			return
		}
		mutex.Lock()
		*requests = append(*requests, secondaryHTTPRequest{r.Method, key, r.Header.Get("Prefix"), decoded})
		index := counts[key]
		counts[key]++
		mutex.Unlock()
		raw, ok := c.Pages[key]
		if !ok {
			t.Error("request left reference resources", key)
			w.WriteHeader(500)
			return
		}
		if raw[0] == '[' {
			var rows []json.RawMessage
			if json.Unmarshal(raw, &rows) != nil || len(rows) == 0 {
				t.Error("bad sequence")
				w.WriteHeader(500)
				return
			}
			raw = rows[min(index, len(rows)-1)]
		}
		var response struct {
			Status  int
			Body    string
			Headers map[string]string
		}
		if json.Unmarshal(raw, &response) != nil {
			t.Error("bad response fixture")
			w.WriteHeader(500)
			return
		}
		for k, v := range response.Headers {
			w.Header().Set(k, v)
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(response.Status)
		fmt.Fprint(w, response.Body)
	}))
}
func TestSecondaryProvidersMatchActualPythonHTTPRequestsAndOutput(t *testing.T) {
	profiles := map[string]string{"softgarden": "softgarden.inline-urls/v1", "ukg": "ukg.search-items/v1", "bamboohr": "bamboohr.careers-list/v1", "recruiter_co_kr": "recruiter-co-kr.jobflex/v1"}
	for _, c := range secondaryHTTPCases(t) {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			requests := []secondaryHTTPRequest{}
			var mutex sync.Mutex
			client := secondaryHTTPFixture(t, c, &requests, &mutex)
			config := map[string]string{"crawler_type": c.Provider, "monitor_needs_browser": "0", "board_url": c.BoardURL, "metadata": string(c.Metadata)}
			p := queue.GreenhouseMonitorProfile{Provider: c.Provider, Profile: profiles[c.Provider], Endpoint: c.Endpoint}
			var got RichDiscovery
			var err error
			switch c.Provider {
			case "softgarden":
				got, err = discoverSoftgardenInventory(context.Background(), client.client, p, config)
			case "ukg":
				got, err = discoverUKGInventoryWithWait(context.Background(), client.client, p, config, noSecondaryWait)
			case "bamboohr":
				got, err = discoverBambooHRInventoryWithWait(context.Background(), client.client, p, config, noSecondaryWait)
			default:
				got, err = discoverRecruiterKRInventoryWithWait(context.Background(), client.client, p, config, noSecondaryWait)
			}
			var failure *DiscoveryError
			gone := errors.As(err, &failure) && failure.Kind == "provider_gone"
			if (err != nil) != c.Expected.Error || gone != c.Expected.Gone {
				t.Fatalf("failure differs: %v gone=%v", err, gone)
			}
			mutex.Lock()
			observed := append([]secondaryHTTPRequest{}, requests...)
			mutex.Unlock()
			if !reflect.DeepEqual(observed, c.Requests) {
				t.Fatalf("request contract changed: actual=%v expected=%v", observed, c.Requests)
			}
			if err != nil {
				return
			}
			urls := []string{}
			fields := []map[string]any{}
			for _, j := range got.Jobs {
				urls = append(urls, j.URL)
				if j.URLOnly {
					continue
				}
				fields = append(fields, map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "language": j.Language, "metadata": j.Metadata, "extras": j.Extras})
			}
			sort.Strings(urls)
			sort.Slice(fields, func(i, j int) bool { return fields[i]["url"].(string) < fields[j]["url"].(string) })
			raw, _ := json.Marshal(fields)
			var normalized []map[string]any
			json.Unmarshal(raw, &normalized)
			if got.Truncated != c.Expected.Truncated || !reflect.DeepEqual(urls, c.Expected.URLs) || !reflect.DeepEqual(normalized, c.Expected.Jobs) {
				t.Fatalf("inventory projection changed: actual=%s expected=%v", raw, c.Expected.Jobs)
			}
		})
	}
}
