package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"sync"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type smartCanonicalCase struct {
	Name      string
	BoardURL  string `json:"board_url"`
	Metadata  map[string]any
	Responses map[string][]json.RawMessage
	Requests  map[string]int
	Expected  struct {
		Error, Truncated bool
		Jobs             []map[string]any
	}
}

func smartCanonicalCases(t *testing.T) []smartCanonicalCase {
	t.Helper()
	body, err := os.ReadFile("testdata/python_smart_canonical_worker.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []smartCanonicalCase
	d := json.NewDecoder(bytes.NewReader(body))
	d.UseNumber()
	if d.Decode(&cases) != nil || len(cases) != 8 {
		t.Fatal("original canonical/CPU corpus unavailable")
	}
	return cases
}

func smartCanonicalHTTP(t *testing.T, c smartCanonicalCase, counts map[string]int, mutex *sync.Mutex) *VerifiedHTTP {
	t.Helper()
	return verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		endpoint := "https://" + r.Host + r.URL.RequestURI()
		values := c.Responses[endpoint]
		if r.Method != "GET" || len(values) == 0 {
			t.Error("request left original tenant resources", endpoint)
			w.WriteHeader(400)
			return
		}
		mutex.Lock()
		index := counts[endpoint]
		counts[endpoint]++
		mutex.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(values[min(index, len(values)-1)])
	}))
}

func TestSmartCanonicalHTTPRetainsOriginalFieldsAndLocales(t *testing.T) {
	for _, c := range smartCanonicalCases(t) {
		t.Run(c.Name, func(t *testing.T) {
			md, _ := json.Marshal(c.Metadata)
			config := map[string]string{"board_url": c.BoardURL, "metadata": string(md)}
			counts := map[string]int{}
			var mu sync.Mutex
			client := smartCanonicalHTTP(t, c, counts, &mu)
			token, _ := c.Metadata["token"].(string)
			p := queue.GreenhouseMonitorProfile{Provider: "smartrecruiters", Profile: "smartrecruiters.canonical-items/v1", Token: token, Endpoint: "https://api.smartrecruiters.com/v1/companies/" + token + "/postings?limit=100&offset=0"}
			result, err := discoverAPIInventory(context.Background(), client.client, p, config)
			if (err != nil) != c.Expected.Error || result.Truncated != c.Expected.Truncated {
				t.Fatal("original failure/completeness differs", err)
			}
			if err != nil {
				if len(result.Jobs) != 0 {
					t.Fatal("failed inventory retained a rich prefix")
				}
				return
			}
			if !reflect.DeepEqual(counts, c.Requests) || len(result.Jobs) != len(c.Expected.Jobs) {
				t.Fatal("original request or job count differs", counts, c.Requests)
			}
			byURL := map[string]map[string]any{}
			for _, want := range c.Expected.Jobs {
				byURL[want["url"].(string)] = want
			}
			for _, job := range result.Jobs {
				want := byURL[job.URL]
				if want == nil {
					t.Fatal("unexpected canonical destination", job.URL)
				}
				actual := map[string]any{"url": job.URL, "source_identity": nullableNextdataIdentity(job.SourceIdentity), "title": job.Title, "description": job.Description, "locations": job.Locations, "language": job.Language, "employment_type": job.EmploymentType, "job_location_type": job.JobLocationType, "date_posted": job.DatePosted, "metadata": job.Metadata, "extras": job.Extras}
				raw, _ := json.Marshal(actual)
				var normalized map[string]any
				json.Unmarshal(raw, &normalized)
				for key, value := range normalized {
					if !reflect.DeepEqual(value, want[key]) {
						t.Fatal("original rich field differs", key, fmt.Sprint(value), fmt.Sprint(want[key]))
					}
				}
				locales := []string{}
				if language, ok := job.Language.(string); ok {
					locales = append(locales, language)
				} else {
					locales = append(locales, "en")
				}
				for _, locale := range job.LocalizationLocales {
					present := false
					for _, seen := range locales {
						present = present || seen == locale
					}
					if !present {
						locales = append(locales, locale)
					}
				}
				encoded, _ := json.Marshal(locales)
				var normalizedLocales any
				json.Unmarshal(encoded, &normalizedLocales)
				if !reflect.DeepEqual(normalizedLocales, want["locales"]) {
					t.Fatal("original locale order differs", locales, want["locales"])
				}
			}
		})
	}
}
