package worker

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"strconv"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestDayforcePaginationMatchesActualPython(t *testing.T) {
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_dayforce_pagination.json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus []struct {
		Name     string
		Overlap  int
		Pages    map[string]json.RawMessage
		Requests []int
		Expected struct {
			Error, Truncated bool
			Jobs             []map[string]any
		}
	}
	if json.Unmarshal(raw, &corpus) != nil || len(corpus) != 14 {
		t.Fatal("actual Python pagination corpus unavailable")
	}
	b := api.DayforceBoard{Tenant: "fixture", Portal: "Careers"}
	site := api.DayforceSite{JobBoardID: 7, Culture: "en-US", Cultures: []string{"en-US", "fr-CA"}}
	for _, c := range corpus {
		t.Run(c.Name, func(t *testing.T) {
			requests := []int{}
			counts := map[int]int{}
			out, e := discoverDayforcePages(context.Background(), b, site, c.Overlap, func(_ context.Context, offset int) (*api.Document, *GreenhouseResponse, error) {
				requests = append(requests, offset)
				raw, ok := c.Pages[strconv.Itoa(offset)]
				if !ok {
					t.Fatal("pagination left frozen offsets", offset)
				}
				if len(raw) > 0 && raw[0] == '[' {
					var choices []json.RawMessage
					if json.Unmarshal(raw, &choices) != nil || len(choices) == 0 {
						t.Fatal("invalid sequence")
					}
					raw = choices[min(counts[offset], len(choices)-1)]
				}
				counts[offset]++
				d, e := api.Decode(raw)
				return d, nil, e
			})
			if !reflect.DeepEqual(requests, c.Requests) {
				t.Fatalf("requested offsets=%v expected=%v", requests, c.Requests)
			}
			if c.Expected.Error {
				if e == nil || len(out.Jobs) != 0 {
					t.Fatal("failed prefix became authoritative inventory")
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			if out.Truncated != c.Expected.Truncated || len(out.Jobs) != len(c.Expected.Jobs) {
				t.Fatalf("inventory differs: jobs=%d truncated=%v", len(out.Jobs), out.Truncated)
			}
			for i, job := range out.Jobs {
				fields := map[string]any{"url": job.URL, "title": job.Title, "description": job.Description, "locations": job.Locations, "date_posted": job.DatePosted, "job_location_type": job.JobLocationType, "language": job.Language, "metadata": job.Metadata}
				want := map[string]any{}
				for key := range fields {
					want[key] = c.Expected.Jobs[i][key]
				}
				body, _ := json.Marshal(fields)
				var got map[string]any
				json.Unmarshal(body, &got)
				if !reflect.DeepEqual(got, want) {
					t.Fatalf("job %d actual=%s expected=%v", i, body, want)
				}
			}
		})
	}
}
