package b0task

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestCanonicalTasksMatchPython(t *testing.T) {
	data, err := os.ReadFile("testdata/python_tasks.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct{ Name, Payload, SHA256, SHA1 string }
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	route := Route{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: "go"}
	for _, c := range fixture.Cases {
		t.Run(c.Name, func(t *testing.T) {
			task, err := DecodeCanonical(c.Payload, c.SHA256, route)
			if err != nil || task.PayloadSHA1 != c.SHA1 {
				t.Fatalf("Python task mismatch: %v", err)
			}
			for _, payload := range []string{
				strings.Replace(c.Payload, `"scraper_step":0,`, "", 1),
				strings.Replace(c.Payload, `"initial_ready_at_ms":0,`, "", 1),
				strings.Replace(c.Payload, `"task_kind":"scrape"`, `"task_kind": "scrape"`, 1),
				strings.Replace(c.Payload, `https://jobs.example.test/posting`, `https://jobs.example.test/a b`, 1),
				strings.Replace(c.Payload, `"engine_owner":"go"`, `"engine_owner":"python"`, 1),
				strings.Replace(c.Payload, `"routing_epoch":7`, `"routing_epoch":8`, 1),
			} {
				digest := sha256.Sum256([]byte(payload))
				if _, err := DecodeCanonical(payload, hex.EncodeToString(digest[:]), route); err == nil {
					t.Fatalf("accepted noncanonical or changed task: %s", payload)
				}
			}
		})
	}
}
