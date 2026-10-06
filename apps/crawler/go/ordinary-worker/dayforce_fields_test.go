package worker

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestDayforceRichFieldsMatchActualPython(t *testing.T) {
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_dayforce.json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus []struct {
		Kind, Name string
		Value      any
		Expected   map[string]any
	}
	// Expected values for the other fixture kinds are not maps.
	var all []json.RawMessage
	if json.Unmarshal(raw, &all) != nil || len(all) != 68 {
		t.Fatal("actual Python Dayforce corpus unavailable")
	}
	for _, raw := range all {
		var kind struct{ Kind string }
		json.Unmarshal(raw, &kind)
		if kind.Kind != "field" {
			continue
		}
		var c struct {
			Kind, Name string
			Value      any
			Expected   map[string]any
		}
		d := json.NewDecoder(strings.NewReader(string(raw)))
		d.UseNumber()
		if d.Decode(&c) != nil {
			t.Fatal("invalid field reference")
		}
		corpus = append(corpus, c)
	}
	if len(corpus) != 17 {
		t.Fatal("field references lost")
	}
	b := api.DayforceBoard{Tenant: "fixture", Portal: "Careers"}
	site := api.DayforceSite{JobBoardID: 7, Culture: "en-US", Cultures: []string{"en-US", "fr-CA"}}
	for _, c := range corpus {
		t.Run(c.Name, func(t *testing.T) {
			fields := api.DayforceJobFields(c.Value, b, site)
			if c.Expected == nil {
				if fields != nil {
					t.Fatal("invalid job became content")
				}
				return
			}
			job, e := secondaryRichJob(fields)
			if e != nil {
				t.Fatal(e)
			}
			out := map[string]any{"url": job.URL, "title": job.Title, "description": job.Description, "locations": job.Locations, "date_posted": job.DatePosted, "job_location_type": job.JobLocationType, "language": job.Language, "metadata": job.Metadata}
			want := map[string]any{}
			for key := range out {
				want[key] = c.Expected[key]
			}
			body, _ := json.Marshal(out)
			d, e := api.Decode(body)
			if e != nil {
				t.Fatal(e)
			}
			if !reflect.DeepEqual(d.Value, want) {
				t.Fatalf("actual=%s expected=%v", body, want)
			}
		})
	}
}
