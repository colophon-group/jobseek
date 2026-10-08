package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestSuccessFactorsDetailFieldsMatchActualPythonComponentCorpus(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_sf_detail_fields.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Cases []struct {
			Name, HTML string
			Config     map[string]any
			Input      []struct {
				URL      string
				Metadata map[string]any
			}
			Expected []map[string]any
			Calls    []string
			Error    bool
		}
	}
	if err = json.Unmarshal(raw, &corpus); err != nil {
		t.Fatal(err)
	}
	if len(corpus.Cases) != 7 {
		t.Fatal("actual reference cases missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			c.Config["preset"] = "successfactors"
			c.Config["scraper_type"] = "skip"
			md, err := json.Marshal(c.Config)
			if err != nil {
				t.Fatal(err)
			}
			config := map[string]string{"crawler_type": "rss", "metadata": string(md)}
			p := queue.GreenhouseMonitorProfile{Provider: "rss", RSSDetailEnrichment: true, Endpoint: "https://example.com/googlefeed.xml"}
			inventory := RichDiscovery{}
			for _, input := range c.Input {
				inventory.Jobs = append(inventory.Jobs, RichMonitorJob{URL: input.URL, Metadata: input.Metadata})
			}
			calls := []string{}
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls = append(calls, "https://"+r.Host+r.URL.String())
				fmt.Fprint(w, c.HTML)
			})
			result, err := enrichRSSDetailFields(context.Background(), client.client, p, config, inventory)
			if (err != nil) != c.Error || !reflect.DeepEqual(calls, c.Calls) {
				t.Fatal("actual Python outcome/request parity differs", result, err, calls, c.Calls)
			}
			if c.Error {
				if len(result.Jobs) != 0 {
					t.Fatal("required property failure published prefix")
				}
				return
			}
			for i, expected := range c.Expected {
				if !reflect.DeepEqual(result.Jobs[i].Metadata, expected) {
					t.Fatal("Python Lexbor field text differs", result.Jobs[i].Metadata, expected)
				}
			}
		})
	}
}
