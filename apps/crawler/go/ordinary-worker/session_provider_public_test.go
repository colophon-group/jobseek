package worker

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestSessionProviderPublicCanonicalDescriptions(t *testing.T) {
	dir, native := os.Getenv("JOBSEEK_SESSION_PUBLIC_CAPTURE_DIR"), os.Getenv("JOBSEEK_SESSION_NATIVE_CAPTURE_DIR")
	if dir == "" || native == "" {
		t.Skip("requires private original and native complete inventories")
	}
	for _, slug := range []string{"adm-early-careers-na", "caterpillar-progressrail-us", "adm-us", "adm-global"} {
		t.Run(slug, func(t *testing.T) {
			raw, e := os.ReadFile(filepath.Join(dir, slug+"-original.json"))
			var original struct {
				Status string
				Source string `json:"source_revision"`
				Jobs   []api.Job
				Config struct {
					BoardURL string `json:"board_url"`
				}
				Truncated bool
			}
			if e != nil || json.Unmarshal(raw, &original) != nil || original.Status != "succeeded" || original.Truncated || original.Source != "a5ecb5e9da5057cf8b6348f6cb203628347b5fc7" {
				t.Fatal("original complete inventory unavailable")
			}
			nativeRaw, e := os.ReadFile(filepath.Join(native, slug+"-native.json"))
			var capture struct {
				OriginalSHA string          `json:"original_sha256"`
				Response    replay.Response `json:"response"`
			}
			digest := sha256.Sum256(raw)
			if e != nil || json.Unmarshal(nativeRaw, &capture) != nil || capture.OriginalSHA != hex.EncodeToString(digest[:]) {
				t.Fatal("native/original binding differs")
			}
			profile := queue.GreenhouseMonitorProfile{Provider: "brassring", Profile: "brassring.session-items/v1", Endpoint: original.Config.BoardURL, EffectiveConfigSHA256: capture.Response.ConfigFingerprint}
			result, e := parseAPIReplayResponse(profile, capture.Response)
			if e != nil || result.Truncated || len(result.Jobs) != len(original.Jobs) {
				t.Fatal("complete native public worker inventory unavailable")
			}
			byURL := map[string]api.Job{}
			for _, job := range original.Jobs {
				byURL[job.URL] = job
			}
			for _, job := range result.Jobs {
				want, ok := byURL[job.URL]
				if !ok {
					t.Fatal("native foreign identity")
				}
				description, err := executor.CoerceText(want.Description)
				if err != nil || !reflect.DeepEqual(job.Description, description) {
					t.Fatal("original canonical description differs")
				}
			}
			t.Log("all original normalized public descriptions match", len(result.Jobs))
		})
	}
}
