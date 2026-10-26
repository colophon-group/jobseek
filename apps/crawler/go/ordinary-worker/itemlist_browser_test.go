package worker

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"reflect"
	"sort"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestItemListHeldStreamMatchesActualPythonDiscovery(t *testing.T) {
	body, err := os.ReadFile("../api-sniffer-monitor/testdata/python_itemlist_browser.json")
	var corpus struct {
		Metadata json.RawMessage
		Cases    []struct {
			Name, HTML string
			Error      bool
			Chunks     []any
		}
	}
	if err != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) != 18 {
		t.Fatal("actual Python browser-source corpus missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			config := map[string]string{"crawler_type": "nextdata", "monitor_needs_browser": "1", "metadata": string(corpus.Metadata), "board_url": "https://example.com/careers"}
			p := queue.GreenhouseMonitorProfile{Provider: "nextdata", Profile: "nextdata.rendered-urls/v1", Endpoint: config["board_url"]}
			o, _, err := queue.RenderedNextdataMonitorOptions(config)
			if err != nil {
				t.Fatal(err)
			}
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("browser-source monitor fell back to HTTP") })
			chunks := []any{}
			_, err = discoverNextdataWithPages(context.Background(), client.client, p, config, func(jobs []RichMonitorJob) error {
				rows := []map[string]any{}
				for _, j := range jobs {
					if len(o.Fields) == 0 {
						rows = append(rows, map[string]any{"url": j.URL})
						continue
					}

					rows = append(rows, map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "metadata": j.Metadata, "language": j.Language, "extras": nil, "localizations": nil, "base_salary": nil, "source_identity": nullableNextdataIdentity(j.SourceIdentity)})
				}
				sort.Slice(rows, func(i, j int) bool { return rows[i]["url"].(string) < rows[j]["url"].(string) })
				encoded, _ := json.Marshal(rows)
				var values any
				_ = json.Unmarshal(encoded, &values)
				chunks = append(chunks, values)
				return nil
			}, func(ctx context.Context, endpoint string) nextdataPage {
				return parseHeldNextdataPage(ctx, p, o, heldRenderedResult(c.HTML, endpoint, 200))
			})
			if (err != nil) != c.Error || !reflect.DeepEqual(chunks, c.Chunks) {
				t.Fatal("original browser-source stream differs", err != nil, c.Error, len(chunks), len(c.Chunks))
			}
		})
	}
}
