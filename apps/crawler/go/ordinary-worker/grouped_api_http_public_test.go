package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Source migration candidates use the actual sealed Go transport and the
// existing HTTP collector. Original browser/proxy controls remain in the
// separate reference; this test grants no configuration or database authority.
func TestGroupedAPIHTTPPublicReplacement(t *testing.T) {
	input, originalDir, out := os.Getenv("JOBSEEK_HTTP_REPLACEMENT_INPUT"), os.Getenv("JOBSEEK_HTTP_REPLACEMENT_ORIGINAL_DIR"), os.Getenv("JOBSEEK_HTTP_REPLACEMENT_OUTPUT_DIR")
	if input == "" || originalDir == "" || out == "" {
		t.Skip("requires protected candidate and original evidence")
	}
	data, e := os.ReadFile(input)
	var candidate struct {
		Records []struct {
			Slug, ID string
			Config   map[string]string `json:"canonical_candidate"`
		}
	}
	if e != nil || json.Unmarshal(data, &candidate) != nil || len(candidate.Records) != 2 {
		t.Fatal("bounded two-board candidate unavailable")
	}
	client, e := NewVerifiedDirectHTTP(DirectHTTPConfig{CABundlePEM: pinnedCA})
	if e != nil {
		t.Fatal(e)
	}
	defer client.CloseIdleConnections()
	for _, record := range candidate.Records {
		t.Run(record.Slug, func(t *testing.T) {
			if record.Slug != "sage-careers-search" && record.Slug != "mckinsey-careers" {
				t.Fatal("candidate outside selected public pair")
			}
			raw, e := os.ReadFile(filepath.Join(originalDir, record.Slug+"-original.json"))
			var original struct {
				Status    string
				Truncated bool
				Jobs      []map[string]any
				Source    string `json:"source_revision"`
			}
			if e != nil || json.Unmarshal(raw, &original) != nil || original.Status != "succeeded" || original.Truncated || original.Source != "a5ecb5e9da5057cf8b6348f6cb203628347b5fc7" {
				t.Fatal("complete original public reference unavailable")
			}
			profile, e := queue.InspectRichMonitor(record.ID, record.Config)
			if e != nil {
				t.Fatal("explicit HTTP candidate not supported", e)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
			defer cancel()
			result, e := discoverAPISnifferInventory(ctx, client.client, profile, record.Config)
			if e != nil || result.Truncated || len(result.Jobs) == 0 {
				t.Fatal("complete native public HTTP inventory unavailable", e)
			}
			actual := []map[string]any{}
			for _, job := range result.Jobs {
				row := map[string]any{"url": job.URL, "title": job.Title, "description": job.Description, "locations": job.Locations, "metadata": job.Metadata, "extras": job.Extras, "base_salary": job.BaseSalary, "date_posted": job.DatePosted, "employment_type": job.EmploymentType, "job_location_type": job.JobLocationType}
				body, _ := json.Marshal(row)
				var normalized map[string]any
				json.Unmarshal(body, &normalized)
				actual = append(actual, normalized)
			}
			for _, jobs := range [][]map[string]any{original.Jobs, actual} {
				for _, job := range jobs {
					for key, value := range job {
						if value == nil {
							delete(job, key)
						} else if (key == "metadata" || key == "extras") && len(value.(map[string]any)) == 0 {
							delete(job, key)
						}
					}
				}
				sort.Slice(jobs, func(i, j int) bool { return jobs[i]["url"].(string) < jobs[j]["url"].(string) })
			}
			digest := sha256.Sum256(raw)
			proof := map[string]any{"slug": record.Slug, "original_sha256": hex.EncodeToString(digest[:]), "observed_at_utc": time.Now().UTC().Format(time.RFC3339Nano), "original_jobs": len(original.Jobs), "native_jobs": len(actual), "all_original_fields_match": reflect.DeepEqual(original.Jobs, actual), "canonical_candidate": record.Config, "jobs": actual, "production_mutation": false}
			file, e := os.OpenFile(filepath.Join(out, record.Slug+"-native.json"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if e != nil {
				t.Fatal("private output exists or unavailable")
			}
			e = json.NewEncoder(file).Encode(proof)
			syncErr := file.Sync()
			closeErr := file.Close()
			if e != nil || syncErr != nil || closeErr != nil {
				t.Fatal("private evidence persistence failed")
			}
			if !reflect.DeepEqual(original.Jobs, actual) {
				changed := map[string]int{}
				if len(original.Jobs) == len(actual) {
					for i, want := range original.Jobs {
						for key, value := range want {
							if !reflect.DeepEqual(value, actual[i][key]) {
								changed[key]++
							}
						}
					}
				}
				t.Fatal("public URL/field parity differs", len(original.Jobs), len(actual), changed)
			}
			t.Log("complete native HTTP inventory matches every original field", len(actual))
		})
	}
}
