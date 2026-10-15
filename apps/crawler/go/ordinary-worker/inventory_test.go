package worker

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"

	greenhouse "github.com/colophon-group/jobseek/apps/crawler/go/greenhouse-monitor"
)

func TestGreenhouseURLPythonOracle(t *testing.T) {
	body, err := os.ReadFile("testdata/python_urls.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct{ URL, Canonical, Reason string }
	if err := json.Unmarshal(body, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) < 35 {
		t.Fatal("missing actual Python URL cases")
	}
	for _, item := range cases {
		t.Run(item.URL, func(t *testing.T) {
			canonical := canonicalJobURL(item.URL)
			reason := classifyJobURL(canonical, "https://job-boards.greenhouse.io/fixture")
			if canonical != item.Canonical || reason != item.Reason {
				t.Fatalf("Python URL mismatch: canonical=%q want=%q reason=%s want=%s", canonical, item.Canonical, reason, item.Reason)
			}
		})
	}
}

func TestGreenhouseInventoryPythonOracle(t *testing.T) {
	body, err := os.ReadFile("testdata/python_inventory.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name        string
		BoardURL    string            `json:"board_url"`
		RawJobs     []json.RawMessage `json:"raw_jobs"`
		Truncated   bool
		Discovered  int
		DropReasons map[string]int `json:"drop_reasons"`
		Jobs        []greenhouse.Job
	}
	if err := json.Unmarshal(body, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) != 6 {
		t.Fatal("missing captured Python inventory cases")
	}
	for _, item := range cases {
		t.Run(item.Name, func(t *testing.T) {
			payload, err := json.Marshal(map[string]any{"jobs": item.RawJobs})
			if err != nil {
				t.Fatal(err)
			}
			parsed, err := greenhouse.Parse(payload)
			if err != nil {
				t.Fatal(err)
			}
			parsed.Truncated = item.Truncated
			got, err := NormalizeGreenhouseInventory(context.Background(), item.BoardURL, parsed)
			if err != nil || got.Discovered != item.Discovered || got.Truncated != item.Truncated || !reflect.DeepEqual(got.DropReasons, item.DropReasons) || len(got.Jobs) != len(item.Jobs) {
				t.Fatalf("Python inventory accounting mismatch: %+v error=%v", got, err)
			}
			for i, job := range got.Jobs {
				want := item.Jobs[i]
				if job.URL != want.URL || !reflect.DeepEqual(job.Title, want.Title) || !reflect.DeepEqual(job.Description, want.Description) || !reflect.DeepEqual(job.Locations, want.Locations) || !reflect.DeepEqual(job.Language, want.Language) {
					t.Fatalf("last dictionary content/canonical source mismatch at %d: %+v expected %+v", i, job, want)
				}
			}
		})
	}
}

func TestGreenhouseInventoryRetainsAllAboveCapAndRejectsCancellation(t *testing.T) {
	jobs := make([]greenhouse.Job, greenhouse.MaxJobs+1)
	for i := range jobs {
		jobs[i].URL = "https://example.com/jobs/" + string(rune(i+0x10000))
	}
	got, err := NormalizeGreenhouseInventory(context.Background(), "https://job-boards.greenhouse.io/fixture", greenhouse.Inventory{Jobs: jobs, Truncated: true})
	if err != nil || len(got.Jobs) != len(jobs) || got.Discovered != len(jobs) || !got.Truncated {
		t.Fatal("truncated inventory sliced collected jobs")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if result, err := NormalizeGreenhouseInventory(ctx, "", greenhouse.Inventory{Jobs: jobs}); !errors.Is(err, context.Canceled) || len(result.Jobs) != 0 {
		t.Fatal("cancelled inventory supplied partial success")
	}
}

func TestMideaSPAIdentityPreservesJobsWithoutAdmittingHomepages(t *testing.T) {
	const board = "https://recruit.midea.com/recruitOut/ihr/home/index"
	const id = "1234567890abcdef1234567890abcdef"
	source := board + "#/positionDetail/" + id
	found, err := NormalizeRichInventory(context.Background(), board, []RichMonitorJob{{URL: source, URLOnly: true}}, true)
	if err != nil || len(found.Jobs) != 1 || found.Jobs[0].URL != source || !found.Truncated || len(found.DropReasons) != 0 {
		t.Fatal("verified Midea SPA identity was filtered", err)
	}
	for _, fragment := range []string{"0", "", "/positionDetail/", "/positionDetail/" + id[:31], "/positionDetail/" + strings.ToUpper(id), "/positionDetail/" + id + "/", "/positionDetail/" + id + "?other=1", "/other/" + id} {
		if classifyJobURL(board+"#"+fragment, board) != "board_homepage" {
			t.Fatal("unverified Midea fragment accepted")
		}
	}
	for _, other := range []string{strings.Replace(board, "https:", "http:", 1), strings.Replace(board, "recruit.midea.com", "example.com", 1), strings.Replace(board, "recruit.midea.com", "recruit.midea.com:443", 1), strings.Replace(board, "recruit.midea.com", "user:pass@recruit.midea.com", 1), strings.Replace(board, "/index", "/other", 1), board + ";param", board + "?listing=1", board + "#listing"} {
		base := strings.Split(strings.Split(other, "#")[0], "?")[0]
		if classifyJobURL(base+"#/positionDetail/"+id, other) != "board_homepage" {
			t.Fatal("Midea exception escaped verified listing")
		}
	}
}
