//go:build !densitybench

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	"google.golang.org/protobuf/proto"
)

// Explicit read-only public qualification. Raw typed documents stay in the
// private capture directory; normal CI runs isolated physical fixtures instead.
func TestLightpandaDocumentInteractionsPublicOriginalCapture(t *testing.T) {
	directory := os.Getenv("JOBSEEK_INTERACTION_PUBLIC_CAPTURE_DIR")
	if directory == "" {
		t.Skip("requires private original public captures")
	}
	binary := interactionIntegrationBinary(t)
	selected, err := os.ReadFile(filepath.Join(directory, "native1005-interaction-original-selected26-2026-10-10.json"))
	var rows []struct{ Canonical map[string]string }
	if err != nil || json.Unmarshal(selected, &rows) != nil || len(rows) != 26 {
		t.Fatal("canonical selected cohort unavailable")
	}
	for _, row := range rows {
		slug := row.Canonical["board_slug"]
		t.Run(slug, func(t *testing.T) {
			path := filepath.Join(directory, "native1005-interaction-"+slug+"-original-public-capture1-2026-10-10.json")
			original, err := os.ReadFile(path)
			var c struct {
				Status string
				Board  struct {
					URL      string `json:"board_url"`
					Metadata map[string]any
				}
			}
			if err != nil || json.Unmarshal(original, &c) != nil {
				t.Fatal("original public case unavailable")
			}
			pipeline, err := actions.Parse(c.Board.Metadata["actions"])
			if err != nil {
				t.Fatal("canonical original action pipeline rejected")
			}
			wait, timeout := "networkidle", uint64(30000)
			fallback := "domcontentloaded"
			var fallbackWait *string = &fallback
			if v, ok := c.Board.Metadata["wait"].(string); ok {
				wait = v
			}
			if v, ok := c.Board.Metadata["timeout"].(float64); ok {
				timeout = uint64(v)
			}
			if v, exists := c.Board.Metadata["wait_fallback"]; exists {
				if v == nil {
					fallbackWait = nil
				} else {
					fallback = v.(string)
				}
			}
			input, err := lp.NavigationInput(lp.Navigation{URL: c.Board.URL, RoutingRevision: "original-interaction-public-qualification", OriginRequestID: "public-interaction-qualification", Wait: wait, WaitFallback: fallbackWait, TimeoutMS: timeout, TransportRetries: 1})
			if err != nil {
				t.Fatal("canonical navigation rejected")
			}
			payload, _ := proto.Marshal(input)
			digest := sha256.Sum256([]byte(row.Canonical["metadata"]))
			identity := hex.EncodeToString(digest[:])
			ctx, cancel := context.WithTimeout(context.Background(), 150*time.Second+actions.Budget(pipeline))
			defer cancel()
			execution := &runtimeV1ServiceExecution{dayforceConfig: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dayforceRun: runTask}
			response, err := execution.executeDocumentActions(ctx, actions.Request{Protocol: actions.Protocol, RequestID: identity, ConfigFingerprint: identity, Input: payload, Actions: pipeline})
			if err != nil {
				t.Fatal("public typed document execution failed")
			}
			out := filepath.Join(directory, "native1005-interaction-"+slug+"-lightpanda-public-capture1-2026-10-10.pb")
			file, err := os.OpenFile(out, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
			if err != nil {
				t.Fatal("private typed result creation failed")
			}
			_, err = file.Write(response.Result)
			closeErr := file.Close()
			if err != nil || closeErr != nil {
				t.Fatal("private typed result write failed")
			}
			digest = sha256.Sum256(response.Result)
			t.Logf("original_status=%s typed_result_sha256=%x child_cleanup_proved=true", c.Status, digest)
		})
	}
}
