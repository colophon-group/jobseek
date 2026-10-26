//go:build !densitybench

package main

import (
	"context"
	"encoding/json"
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

// Anonymous public qualification is opt-in. These files are evidence, and
// cannot select a process, credential, runtime policy or production owner.
func TestLightpandaLiveAPIPublicOriginalFields(t *testing.T) {
	dir, binary := os.Getenv("JOBSEEK_API_LIVE_PUBLIC_CAPTURE_DIR"), os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if dir == "" || binary == "" {
		t.Skip("requires anonymous original captures and pinned binary")
	}
	expected, supported := lightpandaPinnedSHA256[runtime.GOARCH]
	if runtime.GOOS == "darwin" && runtime.GOARCH == "arm64" {
		expected, supported = lightpandaPinnedDarwinArm64SHA256, true
	} else if runtime.GOOS != "linux" {
		supported = false
	}
	if !supported || verifyFileSHA256(binary, expected) != nil {
		t.Fatal("official asset differs")
	}
	for _, slug := range []string{"hitachi-energy-careers", "innosuisse-main", "ihg-kimpton-americas"} {
		t.Run(slug, func(t *testing.T) {
			raw, err := os.ReadFile(filepath.Join(dir, slug+"-original.json"))
			var original struct {
				Status string
				Config struct {
					BoardURL string `json:"board_url"`
					Metadata json.RawMessage
				}
				Jobs      []api.Job
				Truncated bool
			}
			if err != nil || json.Unmarshal(raw, &original) != nil || original.Status != "succeeded" || original.Truncated {
				t.Fatal("original complete inventory unavailable")
			}
			ctx, cancel := context.WithTimeout(context.Background(), 150*time.Second)
			defer cancel()
			execution := &runtimeV1ServiceExecution{dayforceConfig: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dayforceRun: runTask}
			response, err := execution.executeAPIReplay(ctx, replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: original.Config.BoardURL, Metadata: original.Config.Metadata, TimeoutMS: 120000})
			if err != nil || response.Outcome != "success" {
				t.Fatal("pinned public inventory failed", response.Outcome)
			}
			var inventory api.Inventory
			if json.Unmarshal(response.Inventory, &inventory) != nil || inventory.Truncated {
				t.Fatal("complete native inventory unavailable")
			}
			for _, jobs := range [][]api.Job{original.Jobs, inventory.Jobs} {
				for i := range jobs {
					if jobs[i].Metadata == nil {
						jobs[i].Metadata = map[string]any{}
					}
					if jobs[i].Extras == nil {
						jobs[i].Extras = map[string]any{}
					}
				}
			}
			sort.Slice(original.Jobs, func(i, j int) bool { return original.Jobs[i].URL < original.Jobs[j].URL })
			sort.Slice(inventory.Jobs, func(i, j int) bool { return inventory.Jobs[i].URL < inventory.Jobs[j].URL })
			if len(original.Jobs) != len(inventory.Jobs) || len(original.Jobs) > 0 && !reflect.DeepEqual(original.Jobs, inventory.Jobs) {
				t.Fatal("public URL/field parity differs", "original_count", len(original.Jobs), "native_count", len(inventory.Jobs))
			}
			if output := os.Getenv("JOBSEEK_API_LIVE_NATIVE_CAPTURE_DIR"); output != "" {
				file, err := os.OpenFile(filepath.Join(output, slug+"-native-inventory.json"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
				if err != nil {
					t.Fatal(err)
				}
				_, err = file.Write(response.Inventory)
				closeErr := file.Close()
				if err != nil || closeErr != nil {
					t.Fatal("private inventory capture failed")
				}
			}
			t.Logf("platform=%s/%s release=1.1.0-nightly.11106+659958a24 jobs=%d all_original_fields_match=true child_cleanup_proved=true", runtime.GOOS, runtime.GOARCH, len(inventory.Jobs))
		})
	}
}
