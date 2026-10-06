package worker

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"sort"
	"testing"
)

func TestFeedURLsMatchActualPythonProcessor(t *testing.T) {
	var corpus struct {
		Cases []struct {
			Name           string
			Metadata       json.RawMessage
			Jobs, Expected []RichMonitorJob
		}
	}
	b, err := os.ReadFile("testdata/python_feed_urls.json")
	if err != nil || json.Unmarshal(b, &corpus) != nil || len(corpus.Cases) != 14 {
		t.Fatal("actual processor corpus unavailable", err)
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			got, err := applyFeedMonitorURLs(context.Background(), map[string]string{"metadata": string(c.Metadata)}, c.Jobs)
			if err != nil {
				t.Fatal(err)
			}
			sort.Slice(got, func(i, j int) bool { return got[i].URL < got[j].URL })
			for i := range got {
				if i >= len(c.Expected) || got[i].URL != c.Expected[i].URL || !reflect.DeepEqual(got[i].Title, c.Expected[i].Title) || !reflect.DeepEqual(got[i].Description, c.Expected[i].Description) {
					t.Fatal("processor identity/content differs", got, c.Expected)
				}
			}
			if len(got) != len(c.Expected) {
				t.Fatal("filtered/colliding inventory differs", got, c.Expected)
			}
		})
	}
}

func TestFeedURLProcessingCancellationCannotReturnInventory(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	got, err := applyFeedMonitorURLs(ctx, map[string]string{"metadata": "{}"}, []RichMonitorJob{{URL: "https://example.com/jobs/123"}})
	if err != context.Canceled || !reflect.DeepEqual(got, []RichMonitorJob(nil)) {
		t.Fatal(got, err)
	}
}
