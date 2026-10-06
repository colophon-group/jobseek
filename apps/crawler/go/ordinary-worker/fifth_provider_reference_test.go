package worker

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"io"
	"net/http"
	"os"
	"reflect"
	"sort"
	"sync"
	"testing"
)

type fifthHTTPCase struct {
	Provider, Name, Source string
	Detail                 bool
	Metadata               map[string]any
	Pages                  map[string]json.RawMessage
	Requests               []fourthHTTPRequest
	Expected               struct {
		Error, Gone, Truncated, Empty bool
		Jobs                          []map[string]any
		URLs                          []string
		Content                       map[string]any
		CanonicalDescription          *string `json:"canonical_description"`
	}
}

func fifthHTTPCases(t *testing.T) []fifthHTTPCase {
	t.Helper()
	raw, e := os.ReadFile("testdata/python_fifth_provider_http.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []fifthHTTPCase
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 43 {
		t.Fatal("actual Python fifth HTTP corpus unavailable")
	}
	return cases
}
func fifthHTTPFixture(t *testing.T, c fifthHTTPCase, requests *[]fourthHTTPRequest, mutex *sync.Mutex) *VerifiedDirectHTTP {
	t.Helper()
	counts := map[string]int{}
	return verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Proxy-Authorization") != "" {
			t.Error("proxy credential reached canonical origin fixture")
		}
		source := "https://" + r.Host + r.URL.String()
		rawBody, e := io.ReadAll(r.Body)
		if e != nil {
			t.Error(e)
			w.WriteHeader(500)
			return
		}
		var body any
		if len(rawBody) > 0 && json.Unmarshal(rawBody, &body) != nil {
			t.Error("invalid native POST body")
			w.WriteHeader(500)
			return
		}
		headers := map[string]string{}
		for _, key := range []string{"authorization", "csod-accept-language", "locale", "x-requested-with", "x-forwarded-host", "filepath", "isabsolutepath", "isattachmenttype"} {
			if _, ok := r.Header[http.CanonicalHeaderKey(key)]; ok {
				headers[key] = r.Header.Get(key)
			}
		}
		mutex.Lock()
		*requests = append(*requests, fourthHTTPRequest{r.Method, source, body, headers})
		n := counts[source]
		counts[source]++
		mutex.Unlock()
		raw, ok := c.Pages[source]
		if !ok {
			t.Error("request left frozen resources", source)
			w.WriteHeader(500)
			return
		}
		if len(raw) > 0 && raw[0] == '[' {
			var seq []json.RawMessage
			if json.Unmarshal(raw, &seq) != nil || len(seq) == 0 {
				t.Error("invalid response sequence")
				w.WriteHeader(500)
				return
			}
			raw = seq[min(n, len(seq)-1)]
		}
		var response struct {
			Status  int
			Body    string
			Base64  string `json:"body_base64"`
			Headers map[string]string
		}
		if json.Unmarshal(raw, &response) != nil {
			t.Error("invalid response")
			w.WriteHeader(500)
			return
		}
		for key, value := range response.Headers {
			w.Header().Set(key, value)
		}
		w.WriteHeader(response.Status)
		if response.Base64 != "" {
			b, e := base64.StdEncoding.DecodeString(response.Base64)
			if e != nil {
				t.Error(e)
				return
			}
			w.Write(b)
		} else {
			fmt.Fprint(w, response.Body)
		}
	}))
}
func TestFifthProvidersMatchActualPythonHTTPRequestsAndOutput(t *testing.T) {
	for _, c := range fifthHTTPCases(t) {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			requests := []fourthHTTPRequest{}
			var mutex sync.Mutex
			client := fifthHTTPFixture(t, c, &requests, &mutex)
			var e error
			if c.Detail {
				var fields map[string]any
				if c.Provider == "adp" {
					fields, _, e = fetchADPDetail(context.Background(), client.client, queue.WorkdayDetailProfile{SourceURL: c.Source, HTTPAPIConfig: c.Metadata})
				} else {
					fields, _, e = fetchPaylocityDetail(context.Background(), client.client, c.Source)
				}
				if c.Expected.Error {
					if e == nil {
						t.Fatal("reference error became content")
					}
				} else {
					if e != nil && !(c.Expected.Empty && errors.Is(e, executor.ErrEmptyResult)) {
						t.Fatal("valid detail failed", e)
					}
					out := map[string]any{}
					for _, key := range []string{"title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary", "language", "extras", "metadata"} {
						out[key] = fields[key]
					}
					raw, _ := json.Marshal(out)
					var normalized map[string]any
					json.Unmarshal(raw, &normalized)
					if !reflect.DeepEqual(normalized, c.Expected.Content) {
						t.Fatalf("detail differs: actual=%s expected=%v", raw, c.Expected.Content)
					}
				}
			} else {
				raw, _ := json.Marshal(c.Metadata)
				config := map[string]string{"board_url": c.Source, "crawler_type": c.Provider, "monitor_needs_browser": "0", "metadata": string(raw)}
				p := queue.GreenhouseMonitorProfile{Provider: c.Provider}
				var out RichDiscovery
				switch c.Provider {
				case "adp":
					b, err := api.ADPOptionsFromMetadata(c.Source, string(raw))
					if err != nil {
						t.Fatal(err)
					}
					p.Profile, p.Endpoint = "adp.search-items/v1", b.SearchURL(1)
					out, e = discoverADPInventoryWithWait(context.Background(), client.client, p, config, noSecondaryWait)
				case "cornerstone":
					b, err := api.CornerstoneOptionsFromMetadata(c.Source, string(raw))
					if err != nil {
						t.Fatal(err)
					}
					p.Profile, p.Endpoint = "cornerstone.search-items/v1", b.ListingURL()
					out, e = discoverCornerstoneInventoryWithWait(context.Background(), client.client, p, config, noSecondaryWait)
				case "paylocity":
					p.Profile, p.Endpoint = "paylocity.embedded-items/v1", c.Source
					out, e = discoverPaylocityInventoryWithWait(context.Background(), client.client, p, config, noSecondaryWait)
				}
				if c.Expected.Error || c.Expected.Gone {
					if e == nil {
						t.Fatal("reference failure became inventory")
					}
					var f *DiscoveryError
					if c.Expected.Gone && (!errors.As(e, &f) || f.Kind != "provider_gone") {
						t.Fatal("reference gone classification differs", e)
					}
				} else {
					if e != nil || out.Truncated != c.Expected.Truncated {
						t.Fatal("valid inventory contract differs", e, out.Truncated, c.Expected.Truncated)
					}
					urls := []string{}
					for _, job := range out.Jobs {
						urls = append(urls, job.URL)
						var expected map[string]any
						for _, ref := range c.Expected.Jobs {
							if ref["url"] == job.URL {
								expected = ref
								break
							}
						}
						if expected == nil {
							t.Fatal("unknown source member")
						}
						out := map[string]any{"title": job.Title, "description": job.Description, "locations": job.Locations, "employment_type": job.EmploymentType, "job_location_type": job.JobLocationType, "date_posted": job.DatePosted, "language": job.Language, "metadata": job.Metadata, "extras": job.Extras}
						b, _ := json.Marshal(out)
						var normalized map[string]any
						json.Unmarshal(b, &normalized)
						want := map[string]any{}
						for key := range out {
							want[key] = expected[key]
						}
						if !reflect.DeepEqual(normalized, want) {
							t.Fatalf("monitor fields differ: actual=%s expected=%v", b, want)
						}
					}
					sort.Strings(urls)
					if !reflect.DeepEqual(urls, c.Expected.URLs) {
						t.Fatal("source inventory differs", urls, c.Expected.URLs)
					}
				}
			}
			mutex.Lock()
			observed := append([]fourthHTTPRequest{}, requests...)
			mutex.Unlock()
			if !reflect.DeepEqual(observed, c.Requests) {
				t.Fatalf("HTTP contract differs: actual=%v expected=%v", observed, c.Requests)
			}
		})
	}
}
