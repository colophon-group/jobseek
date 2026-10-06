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
	"strconv"
	"sync"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestAlmaCareerDiscoveryMatchesActualPythonRequestsFallbacksAndInventories(t *testing.T) {
	type request struct {
		Method, URL, Key string
		Body             any
		Headers          map[string]string
	}
	var corpus struct {
		HTTP []struct {
			Name           string
			BoardURL       string `json:"board_url"`
			Metadata       json.RawMessage
			Pages          map[string]json.RawMessage
			Requests       []request
			NativeReserved bool `json:"native_reserved"`
			Expected       struct {
				Error, Gone, Reserved, Truncated bool
				Jobs                             []map[string]any
			}
		}
	}
	body, e := os.ReadFile("testdata/python_almacareer.json")
	if e != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.HTTP) != 32 {
		t.Fatal("actual Python AlmaCareer HTTP corpus missing")
	}
	for _, c := range corpus.HTTP {
		t.Run(c.Name, func(t *testing.T) {
			requests := []request{}
			counts := map[string]int{}
			var mutex sync.Mutex
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				url := "https://" + r.Host + r.URL.String()
				key := url
				raw, e := io.ReadAll(r.Body)
				if e != nil {
					t.Error("fixture request body failed")
					w.WriteHeader(500)
					return
				}
				var body any
				headers := map[string]string{}
				if len(raw) > 0 {
					var payload map[string]any
					if json.Unmarshal(raw, &payload) != nil {
						t.Error("invalid request JSON")
						w.WriteHeader(500)
						return
					}
					body = payload
					variables, ok := payload["variables"].(map[string]any)
					if !ok {
						t.Error("missing GraphQL variables")
						w.WriteHeader(500)
						return
					}
					switch payload["query"] {
					case api.AlmaListingQuery:
						page, ok := variables["page"].(float64)
						if !ok {
							t.Error("invalid listing page")
							w.WriteHeader(500)
							return
						}
						key = "LIST:" + strconv.Itoa(int(page))
					case api.AlmaDetailQuery:
						id, ok := variables["jobId"].(string)
						if !ok {
							t.Error("missing detail identity")
							w.WriteHeader(500)
							return
						}
						key = "DETAIL:" + id
					default:
						t.Error("non-reference GraphQL query")
						w.WriteHeader(500)
						return
					}
					for _, k := range []string{"accept", "content-type", "origin", "referer", "x-api-key"} {
						headers[k] = r.Header.Get(k)
					}
				}
				mutex.Lock()
				requests = append(requests, request{r.Method, url, key, body, headers})
				index := counts[key]
				counts[key]++
				mutex.Unlock()
				page := c.Pages[key]
				if len(page) == 0 {
					t.Error("undeclared resource", key)
					w.WriteHeader(500)
					return
				}
				if page[0] == '[' {
					var values []json.RawMessage
					if json.Unmarshal(page, &values) != nil || len(values) == 0 {
						t.Error("bad sequence fixture")
						w.WriteHeader(500)
						return
					}
					page = values[min(index, len(values)-1)]
				}
				var response struct {
					Body    string
					Status  int
					Headers map[string]string
				}
				if json.Unmarshal(page, &response) != nil {
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
			config := map[string]string{"crawler_type": "almacareer", "monitor_needs_browser": "0", "board_url": c.BoardURL, "metadata": string(c.Metadata)}
			profile := queue.GreenhouseMonitorProfile{Provider: "almacareer", Profile: "almacareer.graphql-items/v1", Endpoint: "https://acme.jobs.cz/"}
			got, e := discoverAlmaInventory(context.Background(), client.client, profile, config)
			var failure *DiscoveryError
			gone := errors.As(e, &failure) && failure.Kind == "provider_gone"
			reserved := got.Response != nil && got.Response.reserved
			if c.NativeReserved {
				if e == nil || !reserved || len(got.Jobs) > 0 {
					t.Fatal("positive publisher policy was swallowed by optional fallback")
				}
			} else if (e != nil) != c.Expected.Error || gone != c.Expected.Gone || reserved != c.Expected.Reserved {
				t.Fatalf("failure authority differs: error=%v gone=%v reserved=%v; reference error=%v gone=%v reserved=%v", e, gone, reserved, c.Expected.Error, c.Expected.Gone, c.Expected.Reserved)
			}
			group := func(rows []request) map[string][]request {
				out := map[string][]request{}
				for _, r := range rows {
					out[r.Key] = append(out[r.Key], r)
				}
				return out
			}
			observed, expected := group(requests), group(c.Requests)
			if c.NativeReserved {
				for resource, rows := range observed {
					ref := expected[resource]
					if len(rows) > len(ref) || !reflect.DeepEqual(rows, ref[:len(rows)]) {
						t.Fatal("policy boundary emitted a non-reference request")
					}
				}
			} else if !reflect.DeepEqual(observed, expected) {
				t.Fatalf("request history differs: actual=%v expected=%v", observed, expected)
			}
			if e != nil {
				if len(got.Jobs) > 0 {
					t.Fatal("failed listing prefix exposed as inventory")
				}
				return
			}
			rows := []map[string]any{}
			for _, j := range got.Jobs {
				rows = append(rows, map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "metadata": j.Metadata, "language": j.Language, "extras": j.Extras, "localizations": nil, "source_identity": nil})
			}
			for _, j := range c.Expected.Jobs {
				delete(j, "base_salary")
			}
			encoded, _ := json.Marshal(rows)
			var normalized []map[string]any
			if json.Unmarshal(encoded, &normalized) != nil {
				t.Fatal("bad native projection")
			}
			if got.Truncated != c.Expected.Truncated || !reflect.DeepEqual(normalized, c.Expected.Jobs) {
				t.Fatalf("inventory differs: native rows=%d, reference rows=%d", len(rows), len(c.Expected.Jobs))
			}
		})
	}
}
