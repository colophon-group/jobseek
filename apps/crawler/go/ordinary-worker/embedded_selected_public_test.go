package worker

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestEmbeddedSelectedOriginalPublicAndLightpandaDetails(t *testing.T) {
	dir := os.Getenv("JOBSEEK_EMBEDDED_SELECTED_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("protected original and physical detail references required")
	}
	raw, e := os.ReadFile(filepath.Join(dir, "ericsson-china-candidate-locale2.private.json"))
	var c struct {
		ID      string
		Config  map[string]string
		Samples []struct {
			URL, Status string
			Content     map[string]any
		}
	}
	if e != nil || json.Unmarshal(raw, &c) != nil || len(c.Samples) != 3 {
		t.Fatal("three original positive details required")
	}
	engine := os.Getenv("JOBSEEK_PUBLIC_LIGHTPANDA_REFERENCE")
	if engine == "" {
		engine = "1.0.2"
	}
	physicalFile, expectedSHA := "ericsson-lightpanda102-details6.private.json", "9f3ee749a55d1abc5f8fdda0d9559c3b2abcec5bff5bfe902bc06c0a89538ec4"
	if engine == "1.0.1" {
		physicalFile, expectedSHA = "ericsson-lightpanda101-details1.private.json", "d4b66ac8484f49d7a49e05b6f9342716c747129527fcbe8ffe36679ffa18dd60"
	} else if engine != "1.0.2" {
		t.Fatal("unsupported official engine reference")
	}
	raw, e = os.ReadFile(filepath.Join(dir, physicalFile))
	var physical struct {
		EngineSHA256 string `json:"engine_sha256"`
		Samples      []struct {
			URL, HTML string
			FinalURL  string `json:"final_url"`
			Status    int
			Complete  bool
		}
	}
	if e != nil || json.Unmarshal(raw, &physical) != nil || physical.EngineSHA256 != expectedSHA || len(physical.Samples) != 3 {
		t.Fatal("verified physical1.0.2 details required")
	}
	for n, original := range c.Samples {
		live := physical.Samples[n]
		if original.Status != "complete" || !live.Complete || live.Status != 200 || live.URL != original.URL {
			t.Fatal("positive original/native source differs")
		}
		p, e := queue.InspectRenderedDetail(c.ID, c.Config, original.URL, queue.Browser)
		if e != nil {
			t.Fatal("actual candidate profile rejected")
		}
		content, _, e := parseHeldDetail(context.Background(), p, heldRenderedResult(live.HTML, live.FinalURL, 200))
		if e != nil {
			t.Fatal("held embedded parse failed")
		}
		b, _ := json.Marshal(content)
		var actual map[string]any
		json.Unmarshal(b, &actual)
		fields := []string{"title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary", "language", "metadata", "extras"}
		differences := []string{}
		for _, k := range fields {
			if !reflect.DeepEqual(actual[k], original.Content[k]) {
				differences = append(differences, k)
			}
		}
		if len(differences) > 0 {
			t.Fatal("original public detail fields changed", n, differences)
		}
	}
	t.Logf("three original positive details match official %s title/description/location and available fields", engine)
}
