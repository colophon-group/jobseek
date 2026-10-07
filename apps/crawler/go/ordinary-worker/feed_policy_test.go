package worker

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestSharedFeedPolicyMatchesActualPython(t *testing.T) {
	var corpus struct {
		Cases []struct {
			Name           string
			Config         json.RawMessage
			Jobs, Expected []RichMonitorJob
			Error          bool
			NativeRefused  bool `json:"native_refused"`
			Rejected       int  `json:"security_rejected"`
		}
	}
	raw, err := os.ReadFile("testdata/python_feed_policy.json")
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.UseNumber()
	if err != nil || decoder.Decode(&corpus) != nil || len(corpus.Cases) != 26 {
		t.Fatal("actual Python shared-policy reference missing", err)
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			config := map[string]string{"metadata": string(c.Config)}
			if c.NativeRefused {
				if _, err := queue.FeedMonitorURLRules(config); err == nil {
					t.Fatal("malformed boundary contract admitted ownership")
				}
				return
			}
			rejected := 0
			got, err := applyFeedMonitorURLs(context.Background(), config, c.Jobs, &rejected)
			if (err != nil) != c.Error {
				t.Fatal("Python policy terminal differs", err, c.Error)
			}
			if c.Error {
				return
			}
			sort.Slice(got, func(i, j int) bool { return got[i].URL < got[j].URL })
			if len(got) != len(c.Expected) || rejected != c.Rejected {
				t.Fatal("Python policy inventory/rejection count differs", len(got), len(c.Expected), rejected, c.Rejected)
			}
			for i, job := range got {
				if !reflect.DeepEqual(job, c.Expected[i]) {
					t.Fatal("Python policy content differs", job, c.Expected[i])
				}
			}
		})
	}
}

func TestSharedFeedPolicyKeepsActualPythonRSSBatchBoundaryAndCommittedPrefix(t *testing.T) {
	var corpus struct {
		Cases []struct {
			Name       string
			Config     json.RawMessage
			Jobs       []RichMonitorJob
			BatchSizes []int `json:"batch_sizes"`
			Rejected   int   `json:"security_rejected"`
			Error      bool
		} `json:"stream_cases"`
	}
	raw, err := os.ReadFile("testdata/python_feed_policy.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Cases) != 4 {
		t.Fatal("actual Python RSS stream reference missing", err)
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			config := map[string]string{"crawler_type": "rss", "board_url": "https://example.com/careers", "metadata": string(c.Config)}
			sink := &pipelineSink{}
			result, summary, rejected, err := writeFeedPolicyInventory(context.Background(), sink, &pipelinePreparer{}, config, RichDiscovery{Jobs: c.Jobs})
			if (err != nil) != c.Error || rejected != c.Rejected || sink.finished {
				t.Fatal("RSS stream terminal semantics changed", err, rejected, c.Rejected, sink.finished)
			}
			expected := []int{}
			count := 0
			for _, n := range c.BatchSizes {
				count += n
				if n > 0 {
					expected = append(expected, n)
				}
			}
			if !reflect.DeepEqual(sink.chunks, expected) || result.Batches.Inserted != count || summary.Discovered != count {
				t.Fatal("RSS prefix/batch conservation changed", sink.chunks, expected, result, summary)
			}
		})
	}
}
