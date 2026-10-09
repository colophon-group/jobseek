package worker

import (
	"context"
	"encoding/json"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"
)

func TestFeedCollisionMatchesOriginalPython(t *testing.T) {
	var corpus struct {
		Cases []struct {
			Name, Provider string
			Config         json.RawMessage
			Jobs, Expected []RichMonitorJob
			Error          bool
			NativeRefused  bool `json:"native_refused"`
		}
	}
	raw, err := os.ReadFile("testdata/python_feed_collision.json")
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.UseNumber()
	if err != nil || decoder.Decode(&corpus) != nil || len(corpus.Cases) != 33 {
		t.Fatal("original Python collision corpus unavailable", err)
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			config := map[string]string{"metadata": string(c.Config), "crawler_type": c.Provider}
			if c.NativeRefused {
				if _, err := queue.FeedMonitorURLRules(config); err == nil {
					t.Fatal("invalid collision policy admitted")
				}
				return
			}
			got, err := applyFeedMonitorURLs(context.Background(), config, c.Jobs)
			if (err != nil) != c.Error {
				t.Fatalf("original terminal differs: %v, want error %v", err, c.Error)
			}
			if err != nil {
				return
			}
			sort.Slice(got, func(i, j int) bool { return got[i].URL < got[j].URL })
			if !reflect.DeepEqual(got, c.Expected) {
				t.Fatalf("original alias/content selection differs:\ngot %#v\nwant %#v", got, c.Expected)
			}
		})
	}
}

func TestFeedCollisionValidatesAllAliasesBeforeWriting(t *testing.T) {
	var corpus struct {
		Cases []struct {
			Name, Provider string
			Config         json.RawMessage
			Jobs           []RichMonitorJob
			Error          bool
		}
	}
	raw, err := os.ReadFile("testdata/python_feed_collision.json")
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.UseNumber()
	if err != nil || decoder.Decode(&corpus) != nil {
		t.Fatal(err)
	}
	for _, c := range corpus.Cases {
		if c.Name != "rss-buffer-limit" && c.Name != "rss-cross-batch-preference" && c.Name != "conflicting-explicit-identity" {
			continue
		}
		t.Run(c.Name, func(t *testing.T) {
			config := map[string]string{"metadata": string(c.Config), "crawler_type": c.Provider, "board_url": "https://example.com/careers"}
			sink := &pipelineSink{}
			result, summary, _, err := writeFeedPolicyInventory(context.Background(), sink, &pipelinePreparer{}, config, RichDiscovery{Jobs: c.Jobs})
			if (err != nil) != c.Error {
				t.Fatal("collision write terminal differs", err)
			}
			if c.Error {
				if len(sink.chunks) != 0 || result.Batches.Inserted != 0 || summary.Discovered != 0 {
					t.Fatal("unverified alias inventory reached SQL")
				}
				return
			}
			if summary.Discovered != 201 || result.Batches.Inserted != 201 {
				t.Fatal("cross-batch inventory did not collapse globally", summary, result)
			}
		})
	}
}
