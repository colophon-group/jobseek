//go:build !densitybench

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"testing"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestCapturedAPIPairPublicOriginalFields(t *testing.T) {
	input, originalDir, output := os.Getenv("JOBSEEK_CAPTURED_API_INPUT"), os.Getenv("JOBSEEK_CAPTURED_API_ORIGINAL_DIR"), os.Getenv("JOBSEEK_CAPTURED_API_OUTPUT_DIR")
	binary := os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if input == "" || originalDir == "" || output == "" || binary == "" {
		t.Skip("requires protected pair candidates, originals and pinned engine")
	}
	expected, supported := lightpandaPinnedSHA256[runtime.GOARCH]
	if runtime.GOOS == "darwin" && runtime.GOARCH == "arm64" {
		expected, supported = "d4b66ac8484f49d7a49e05b6f9342716c747129527fcbe8ffe36679ffa18dd60", true
	} else if runtime.GOOS != "linux" {
		supported = false
	}
	if !supported || verifyFileSHA256(binary, expected) != nil {
		t.Fatal("official engine asset differs")
	}
	var candidates struct {
		Records []struct {
			Slug, Transport string
			Config          map[string]string `json:"canonical_candidate"`
		}
	}
	body, err := os.ReadFile(input)
	if err != nil || json.Unmarshal(body, &candidates) != nil || len(candidates.Records) != 2 {
		t.Fatal("bounded candidate pair unavailable")
	}
	for _, candidate := range candidates.Records {
		if candidate.Transport != "browser" {
			continue
		}
		t.Run(candidate.Slug, func(t *testing.T) {
			if candidate.Slug != "bp-travelcenters" {
				t.Fatal("browser candidate outside bounded pair")
			}
			raw, err := os.ReadFile(filepath.Join(originalDir, candidate.Slug+"-original.json"))
			var original struct {
				Status    string
				Source    string `json:"config_reference_source_revision"`
				Truncated bool
				Jobs      []map[string]any
			}
			if err != nil || json.Unmarshal(raw, &original) != nil || original.Status != "succeeded" || original.Truncated || original.Source != "a5ecb5e9da5057cf8b6348f6cb203628347b5fc7" || len(original.Jobs) == 0 {
				t.Fatal("complete actual original unavailable")
			}
			ctx, cancel := context.WithTimeout(context.Background(), 150*time.Second)
			defer cancel()
			// Record only owned status/classification values. Browser error strings,
			// HTML and request headers may contain session credentials.
			diagnostics := map[string]any{}
			runner := func(call context.Context, config Config, task Task) (Result, error) {
				result, failure := runTask(call, config, task)
				diagnostics["document_status"] = result.Status
				diagnostics["session_settled"] = result.apiReplaySessionSettled
				if failure != nil {
					diagnostics["failure_type"] = fmt.Sprintf("%T", failure)
					diagnostics["capture_failure"] = errors.Is(failure, errReplayCapture)
					diagnostics["cleanup_unproved"] = errors.Is(failure, errCleanupUnproved)
					diagnostics["deadline_exceeded"] = errors.Is(failure, context.DeadlineExceeded)
					var status *replayStatusError
					if errors.As(failure, &status) {
						diagnostics["api_status"] = status.status
					}
				}
				return result, failure
			}
			execution := &runtimeV1ServiceExecution{dayforceConfig: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dayforceRun: runner}
			response, err := execution.executeAPIReplay(ctx, replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: candidate.Config["board_url"], Metadata: json.RawMessage(candidate.Config["metadata"]), TimeoutMS: 120000})
			var inventory api.Inventory
			complete := err == nil && response.Outcome == "success" && json.Unmarshal(response.Inventory, &inventory) == nil && !inventory.Truncated && len(inventory.Jobs) > 0
			actual := []map[string]any{}
			for _, job := range inventory.Jobs {
				encoded, _ := json.Marshal(job)
				var row map[string]any
				if json.Unmarshal(encoded, &row) != nil {
					t.Fatal("public inventory encoding invalid")
				}
				actual = append(actual, row)
			}
			for _, jobs := range [][]map[string]any{original.Jobs, actual} {
				for _, row := range jobs {
					for key, value := range row {
						if value == nil {
							delete(row, key)
						} else if (key == "metadata" || key == "extras") && len(value.(map[string]any)) == 0 {
							delete(row, key)
						}
					}
				}
				sort.Slice(jobs, func(i, j int) bool { return jobs[i]["url"].(string) < jobs[j]["url"].(string) })
			}
			digest := sha256.Sum256(raw)
			matched := complete && reflect.DeepEqual(original.Jobs, actual)
			proof := map[string]any{"slug": candidate.Slug, "original_sha256": hex.EncodeToString(digest[:]), "observed_at_utc": time.Now().UTC().Format(time.RFC3339Nano), "outcome": response.Outcome, "complete": complete, "original_jobs": len(original.Jobs), "native_jobs": len(actual), "all_original_fields_match": matched, "canonical_candidate": candidate.Config, "jobs": actual, "diagnostics": diagnostics, "production_mutation": false}
			file, err := os.OpenFile(filepath.Join(output, candidate.Slug+"-native.json"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if err != nil {
				t.Fatal("protected output already exists or unavailable")
			}
			writeErr := json.NewEncoder(file).Encode(proof)
			syncErr, closeErr := file.Sync(), file.Close()
			if writeErr != nil || syncErr != nil || closeErr != nil {
				t.Fatal("protected evidence persistence failed")
			}
			if !matched {
				t.Fatal("complete public inventory or field parity unavailable", response.Outcome, len(original.Jobs), len(actual), diagnostics)
			}
			t.Log("all original fields match", len(actual))
		})
	}
}
