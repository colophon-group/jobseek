//go:build !densitybench

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

// Opt-in anonymous public observation. Files contain evidence and cannot
// change the process, egress policy, credentials, ownership or production state.
func TestLightpandaSessionPublicOriginalInventory(t *testing.T) {
	dir, out := os.Getenv("JOBSEEK_SESSION_PUBLIC_CAPTURE_DIR"), os.Getenv("JOBSEEK_SESSION_NATIVE_CAPTURE_DIR")
	if dir == "" || out == "" {
		t.Skip("requires private original/native evidence directories")
	}
	binary := interactionIntegrationBinary(t)
	for _, slug := range []string{"adm-early-careers-na", "caterpillar-progressrail-us", "adm-us", "adm-global", "cgi-global-njoyn"} {
		t.Run(slug, func(t *testing.T) {
			raw, e := os.ReadFile(filepath.Join(dir, slug+"-original.json"))
			var original struct {
				Status, Type, SourceRevision string
				Config                       struct {
					BoardURL string `json:"board_url"`
					Metadata json.RawMessage
				}
				Jobs      []api.Job
				Truncated bool
			}
			if e != nil || json.Unmarshal(raw, &original) != nil {
				t.Fatal("bounded original evidence unavailable")
			}
			metadata := original.Config.Metadata
			directCandidate := original.Type == "njoyn"
			if directCandidate {
				metadata = json.RawMessage(`{"scraper_type":"json-ld","wait":"domcontentloaded","timeout":60000,"max_pages":100,"page_wait_ms":4000}`)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 375*time.Second)
			defer cancel()
			diagnostic := map[string]any{}
			execution := &runtimeV1ServiceExecution{dayforceConfig: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dayforceRun: func(ctx context.Context, c Config, task Task) (Result, error) {
				if original.Type == "brassring" && task.APIReplay.sessionConverse != nil {
					converse := task.APIReplay.sessionConverse
					task.APIReplay.sessionConverse = func(ctx context.Context, capture *replayCapture) error {
						var state map[string]any
						if sessionEvaluate(ctx, `(()=>{const e=document.querySelector('#clearResumeJobsBtn');const s=e&&getComputedStyle(e);return {search:!!e,tag:e?e.tagName:null,type:e?e.type:null,form:!!(e&&e.form),ngClick:!!(e&&e.getAttribute('ng-click')),disabled:!!(e&&e.disabled),hidden:!!(e&&e.hidden),display:s?s.display:null,visibility:s?s.visibility:null,sort:!!document.querySelector('#sortBy-button'),ready:document.readyState,scripts:document.scripts.length}})()`, &state) == nil {
							diagnostic["initial_control_state"] = state
						}
						return converse(ctx, capture)
					}
				}
				result, err := runTask(ctx, c, task)
				if err != nil {
					var control *sessionControlError
					if errors.As(err, &control) {
						diagnostic["control_phase"] = control.phase
					}
					diagnostic["deadline"] = errors.Is(err, context.DeadlineExceeded)
					diagnostic["cleanup_unproved"] = errors.Is(err, errCleanupUnproved)
					diagnostic["inventory"] = errors.Is(err, api.ErrInventory)
					diagnostic["snapshot_changed"] = errors.Is(err, api.ErrBrassRingSnapshotChanged) || errors.Is(err, api.ErrNjoynSnapshotChanged)
				}
				return result, err
			}}
			response, e := execution.executeAPIReplay(ctx, replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: original.Config.BoardURL, Metadata: metadata, Provider: original.Type, TimeoutMS: 360000})
			originalSHA := sha256.Sum256(raw)
			caseBody, _ := json.Marshal(struct {
				ObservedAt      string          `json:"observed_at_utc"`
				OriginalSHA     string          `json:"original_sha256"`
				DirectCandidate bool            `json:"direct_candidate_only"`
				Response        replay.Response `json:"response"`
				Diagnostic      map[string]any  `json:"diagnostic"`
			}{time.Now().UTC().Format(time.RFC3339Nano), hex.EncodeToString(originalSHA[:]), directCandidate, response, diagnostic})
			file, writeErr := os.OpenFile(filepath.Join(out, slug+"-native.json"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if writeErr != nil {
				t.Fatal("private capture creation failed")
			}
			_, writeErr = file.Write(caseBody)
			syncErr := file.Sync()
			closeErr := file.Close()
			if writeErr != nil || syncErr != nil || closeErr != nil {
				t.Fatal("private evidence persistence failed")
			}
			if e != nil || response.Outcome != "success" {
				t.Fatal("pinned public session outcome", response.Outcome, diagnostic)
			}
			var inventory api.Inventory
			if json.Unmarshal(response.Inventory, &inventory) != nil || inventory.Truncated || original.Status != "succeeded" || original.Truncated {
				t.Fatal("complete comparable original inventory unavailable")
			}
			for _, jobs := range [][]api.Job{original.Jobs, inventory.Jobs} {
				for i := range jobs {
					// The worker applies the shared canonical HTML normalizer;
					// its separate public test verifies every description below.
					jobs[i].Description = nil
					if jobs[i].Metadata == nil {
						jobs[i].Metadata = map[string]any{}
					}
					if jobs[i].Extras == nil {
						jobs[i].Extras = map[string]any{}
					}
				}
				sort.Slice(jobs, func(i, j int) bool { return jobs[i].URL < jobs[j].URL })
			}
			if !reflect.DeepEqual(original.Jobs, inventory.Jobs) {
				t.Fatal("public identity/field inventory differs", "original", len(original.Jobs), "native", len(inventory.Jobs))
			}
			t.Logf("release=1.1.0-nightly.11227+0eee96bf2 jobs=%d identity_and_non_description_fields_match=true cleanup_proved=true direct_candidate_only=%t", len(inventory.Jobs), directCandidate)
		})
	}
}
