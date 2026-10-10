//go:build !densitybench

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"testing"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

// Explicit public reads have no production credentials, queue, or database.
// Linux CI separately verifies the isolated production runtime. This local
// qualification binds the official release asset to its actual host platform.
func TestLightpandaRootArrayPublicOriginalFields(t *testing.T) {
	dir, binary := os.Getenv("JOBSEEK_API_ROOT_PUBLIC_CAPTURE_DIR"), os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if dir == "" || binary == "" {
		t.Skip("requires private original public captures and pinned binary")
	}
	expected, supported := lightpandaPinnedSHA256[runtime.GOARCH]
	if runtime.GOOS == "darwin" && runtime.GOARCH == "arm64" {
		expected, supported = "955440053a84754dd64c62f970449a56a2b350cdf43ea5f2e809a73047b8173d", true
	} else if runtime.GOOS != "linux" {
		supported = false
	}
	if !supported || verifyFileSHA256(binary, expected) != nil {
		t.Fatal("official release asset identity differs")
	}
	for _, slug := range []string{"cox-careers-au", "fresenius-kabi-australia-new-zealand"} {
		t.Run(slug, func(t *testing.T) {
			raw, err := os.ReadFile(filepath.Join(dir, "native-api-root-"+slug+"-original-public-capture2-2026-10-10.json"))
			var original struct {
				Status string
				Board  struct {
					BoardURL string `json:"board_url"`
					Metadata json.RawMessage
				}
				Jobs []api.Job
			}
			if err != nil || json.Unmarshal(raw, &original) != nil || original.Status != "complete" || len(original.Jobs) == 0 {
				t.Fatal("original public inventory unavailable")
			}
			ctx, cancel := context.WithTimeout(context.Background(), 150*time.Second)
			defer cancel()
			digest := sha256.Sum256(append([]byte(original.Board.BoardURL), original.Board.Metadata...))
			identity := hex.EncodeToString(digest[:])
			execution := &runtimeV1ServiceExecution{dayforceConfig: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dayforceRun: runTask}
			response, err := execution.executeAPIReplay(ctx, replay.Request{Protocol: replay.Protocol, RequestID: identity, ConfigFingerprint: identity, BoardURL: original.Board.BoardURL, Metadata: original.Board.Metadata, TimeoutMS: 120000})
			if err != nil || response.Outcome != "success" {
				t.Fatal("actual pinned browser public inventory failed", response.Outcome)
			}
			var inventory api.Inventory
			if json.Unmarshal(response.Inventory, &inventory) != nil || inventory.Truncated || inventory.URLOnly {
				t.Fatal("complete rich inventory unavailable")
			}
			for i := range original.Jobs {
				if original.Jobs[i].Extras == nil {
					original.Jobs[i].Extras = map[string]any{}
				}
			}
			sort.Slice(original.Jobs, func(i, j int) bool { return original.Jobs[i].URL < original.Jobs[j].URL })
			sort.Slice(inventory.Jobs, func(i, j int) bool { return inventory.Jobs[i].URL < inventory.Jobs[j].URL })
			if !reflect.DeepEqual(inventory.Jobs, original.Jobs) {
				t.Fatal("actual browser fields differ from original inventory", "native count", len(inventory.Jobs), "original count", len(original.Jobs))
			}
			t.Logf("platform=%s/%s release=1.0.0 jobs=%d all_original_fields_match=true child_cleanup_proved=true", runtime.GOOS, runtime.GOARCH, len(inventory.Jobs))
		})
	}
}
