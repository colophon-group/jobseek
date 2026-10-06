package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"io"
	"net/http"
	"os"
	"reflect"
	"sort"
	"sync"
	"testing"
)

type fourthHTTPRequest struct {
	Method, URL string
	Body        any
	Headers     map[string]string
}
type fourthHTTPCase struct {
	Provider, Name, Endpoint string
	BoardURL                 string `json:"board_url"`
	Metadata                 json.RawMessage
	Pages                    map[string]json.RawMessage
	Requests                 []fourthHTTPRequest
	Expected                 struct {
		Error, Gone, Truncated, Hybrid bool
		Jobs                           []map[string]any
		URLs                           []string
	}
}

func fourthHTTPCases(t *testing.T) []fourthHTTPCase {
	t.Helper()
	raw, e := os.ReadFile("testdata/python_fourth_provider_http.json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct{ HTTP []fourthHTTPCase }
	if json.Unmarshal(raw, &corpus) != nil || len(corpus.HTTP) != 29 {
		t.Fatal("actual Python fourth-provider HTTP corpus unavailable")
	}
	return corpus.HTTP
}
func fourthHTTPFixture(t *testing.T, pages map[string]json.RawMessage, requests *[]fourthHTTPRequest, mutex *sync.Mutex) *VerifiedDirectHTTP {
	t.Helper()
	counts := map[string]int{}
	return verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := "https://" + r.Host + r.URL.String()
		body, e := io.ReadAll(r.Body)
		if e != nil {
			t.Error(e)
			w.WriteHeader(500)
			return
		}
		var decoded any
		if len(body) > 0 && json.Unmarshal(body, &decoded) != nil {
			t.Error("invalid request JSON")
			w.WriteHeader(500)
			return
		}
		headers := map[string]string{}
		for _, k := range []string{"authorization", "locale", "translation-highlights", "portal-host-referrer"} {
			if _, exists := r.Header[http.CanonicalHeaderKey(k)]; exists {
				headers[k] = r.Header.Get(k)
			}
		}
		mutex.Lock()
		*requests = append(*requests, fourthHTTPRequest{r.Method, key, decoded, headers})
		n := counts[key]
		counts[key]++
		mutex.Unlock()
		raw, ok := pages[key]
		if !ok {
			t.Error("request left frozen resources", key)
			w.WriteHeader(500)
			return
		}
		if len(raw) > 0 && raw[0] == '[' {
			var seq []json.RawMessage
			if json.Unmarshal(raw, &seq) != nil || len(seq) == 0 {
				t.Error("invalid sequence")
				w.WriteHeader(500)
				return
			}
			raw = seq[min(n, len(seq)-1)]
		}
		var response struct {
			Status  int
			Body    string
			Headers map[string]string
		}
		if json.Unmarshal(raw, &response) != nil {
			t.Error("invalid response")
			w.WriteHeader(500)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		for k, v := range response.Headers {
			w.Header().Set(k, v)
		}
		w.WriteHeader(response.Status)
		fmt.Fprint(w, response.Body)
	}))
}
func TestFourthProvidersMatchActualPythonMonitorHTTPRequestsAndOutput(t *testing.T) {
	for _, c := range fourthHTTPCases(t) {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			requests := []fourthHTTPRequest{}
			var mutex sync.Mutex
			client := fourthHTTPFixture(t, c.Pages, &requests, &mutex)
			config := map[string]string{"crawler_type": c.Provider, "monitor_needs_browser": "0", "board_url": c.BoardURL, "metadata": string(c.Metadata)}
			profile := "rippling.v1-urls/v1"
			if c.Provider == "paycom" {
				profile = "paycom.preview-items/v1"
			}
			p := queue.GreenhouseMonitorProfile{Provider: c.Provider, Profile: profile, Endpoint: c.Endpoint}
			var got RichDiscovery
			var e error
			if c.Provider == "paycom" {
				got, e = discoverPaycomInventoryWithWait(context.Background(), client.client, p, config, noSecondaryWait)
			} else {
				got, e = discoverRipplingInventory(context.Background(), client.client, p, config)
			}
			var f *DiscoveryError
			gone := errors.As(e, &f) && f.Kind == "provider_gone"
			if (e != nil) != c.Expected.Error || gone != c.Expected.Gone {
				t.Fatalf("failure differs: %v gone=%v", e, gone)
			}
			mutex.Lock()
			observed := append([]fourthHTTPRequest{}, requests...)
			mutex.Unlock()
			if !reflect.DeepEqual(observed, c.Requests) {
				t.Fatalf("request contract differs: observed=%v expected=%v", observed, c.Requests)
			}
			if e != nil {
				return
			}
			urls := []string{}
			fields := []map[string]any{}
			for _, j := range got.Jobs {
				urls = append(urls, j.URL)
				if j.URLOnly {
					continue
				}
				if j.Hybrid != c.Expected.Hybrid {
					t.Fatal("hybrid listing contract lost")
				}
				fields = append(fields, map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "language": j.Language, "metadata": j.Metadata, "extras": j.Extras})
			}
			sort.Strings(urls)
			sort.Slice(fields, func(i, j int) bool { return fields[i]["url"].(string) < fields[j]["url"].(string) })
			raw, _ := json.Marshal(fields)
			var normalized []map[string]any
			json.Unmarshal(raw, &normalized)
			if got.Truncated != c.Expected.Truncated || !reflect.DeepEqual(urls, c.Expected.URLs) || !reflect.DeepEqual(normalized, c.Expected.Jobs) {
				t.Fatalf("projection differs: actual=%s expected=%v truncation=%v", raw, c.Expected.Jobs, got.Truncated)
			}
		})
	}
}
