package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/proto"
)

func policyResult(t *testing.T, html string, status uint32) []byte {
	t.Helper()
	body := []byte(html)
	digest := sha256.Sum256(body)
	sha := hex.EncodeToString(digest[:])
	result := &runtimev1.BrowserResult{ContractVersion: runtimeContract, Backend: runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA,
		Outcome: &runtimev1.BrowserResult_Success{Success: &runtimev1.BrowserSuccess{FinalUrl: "https://jobs.example.com/job", Status: &status,
			Html: &runtimev1.ChunkManifest{Complete: true, TotalSizeBytes: uint64(len(body)), TotalSha256: sha,
				Chunks: []*runtimev1.DataChunk{{Sequence: 0, SizeBytes: uint64(len(body)), Sha256: sha, Storage: &runtimev1.DataChunk_InlineBody{InlineBody: body}}}}}}}
	payload, err := proto.MarshalOptions{Deterministic: true}.Marshal(result)
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

type retryHeld struct {
	result []byte
	err    error
	closed bool
	calls  []queueTask
	before func()
}

func (held *retryHeld) close() { held.closed = true }
func (held *retryHeld) execute(ctx context.Context, task queueTask) ([]byte, error) {
	if held.before != nil {
		held.before()
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	held.calls = append(held.calls, task)
	return held.result, held.err
}

type retrySource struct {
	initial *retryHeld
	fresh   *retryHeld
	calls   int
}

func (source *retrySource) reserve(ctx context.Context) (heldReservation, error) {
	source.calls++
	if !source.initial.closed {
		return nil, errors.New("original context still held")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return source.fresh, nil
}

func TestDOMChallengeRetryUsesFreshContextSameFenceAndBoundedOrdinal(t *testing.T) {
	for _, scenario := range []string{"recovers", "still-challenge", "gone", "http-403", "typed-failure", "invalid-html", "authority-lost", "cancelled", "jsonld"} {
		t.Run(scenario, func(t *testing.T) {
			task := validQueueTask(t)
			task.Envelope.ScraperType = "dom"
			if scenario == "jsonld" {
				task.Envelope.ScraperType = "json-ld"
			}
			initial := &retryHeld{result: policyResult(t, "challenge", 200)}
			fresh := &retryHeld{result: policyResult(t, "Engineer", 200)}
			if scenario == "still-challenge" {
				fresh.result = initial.result
			}
			if scenario == "http-403" {
				initial.result = policyResult(t, "challenge", 403)
			}
			if scenario == "typed-failure" {
				initial.err = errors.New("cleanup or transport failure")
			}
			if scenario == "invalid-html" {
				initial.result = bytesReplace(initial.result, "challenge", "challenges")
			}
			queue := &recordingLeaseQueue{}
			if scenario == "authority-lost" {
				queue.heartbeatErr = errors.New("lease lost")
			}
			source := &retrySource{initial: initial, fresh: fresh}
			classified := 0
			s := &supervisor{renderer: source, classifier: func(ctx context.Context, html, url string, config json.RawMessage) (string, error) {
				classified++
				if scenario == "gone" {
					return "gone", nil
				}
				if html == "challenge" {
					return "challenge", nil
				}
				return "okay", nil
			}}
			current := &lease{Task: task, ClaimToken: "7:21", LeaseUntilMS: 20000}
			authority := &leaseAuthority{lease: current, queue: queue, config: config{LeaseTTL: time.Minute}}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if scenario == "cancelled" {
				initial.before = cancel
			}
			result, err := s.renderLease(ctx, initial, current, authority)
			wantRetry := scenario == "recovers" || scenario == "still-challenge"
			if wantRetry {
				if err != nil || source.calls != 1 || !initial.closed || !fresh.closed || len(fresh.calls) != 1 || classified != 2 {
					t.Fatalf("retry lifecycle lost: %v %+v", err, source)
				}
				retried := fresh.calls[0]
				if retried.RenderAttempt != 1 || retried.PayloadSHA256 != task.PayloadSHA256 || retried.Payload != task.Payload || current.ClaimToken != "7:21" || current.Task.RenderAttempt != 0 {
					t.Fatal("retry changed held queue identity")
				}
				first, _ := browserInput(task)
				second, _ := browserInput(retried)
				if first.Plan.Navigation.OriginRequestId == second.Plan.Navigation.OriginRequestId || !strings.HasSuffix(second.Plan.Navigation.OriginRequestId, ":challenge-retry-1") {
					t.Fatal("retry origin binding not distinct")
				}
				if string(result) != string(fresh.result) || queue.heartbeatCalls != 1 || queue.failCalls+queue.terminalCalls+queue.releaseCalls != 0 {
					t.Fatal("retry changed queue schedule or accepted old HTML")
				}
			} else if source.calls != 0 || len(fresh.calls) != 0 {
				t.Fatal("non-challenge contacted another origin context")
			}
			if scenario == "invalid-html" && (err == nil || classified != 0) {
				t.Fatal("unvalidated HTML drove retry classifier")
			}
			if scenario == "authority-lost" {
				var lost *authorityError
				if !errors.As(err, &lost) {
					t.Fatal("retry ignored lost fence")
				}
			}
		})
	}
}

func bytesReplace(body []byte, old, new string) []byte {
	return []byte(strings.ReplaceAll(string(body), old, new))
}

func TestPolicyManifestRejectsUnexpectedUnknownAndCorruptOutput(t *testing.T) {
	for _, mutate := range []func(*runtimev1.BrowserSuccess){
		func(s *runtimev1.BrowserSuccess) { s.Html.Complete = false },
		func(s *runtimev1.BrowserSuccess) { s.Html.TotalSha256 = strings.Repeat("0", 64) },
		func(s *runtimev1.BrowserSuccess) { s.Html.Chunks[0].Sequence = 1 },
		func(s *runtimev1.BrowserSuccess) { s.Html.Chunks[0].Sha256 = strings.Repeat("0", 64) },
		func(s *runtimev1.BrowserSuccess) {
			s.Html.Chunks[0].ProtoReflect().SetUnknown([]byte{0x80, 0x01, 0x01})
		},
		func(s *runtimev1.BrowserSuccess) { s.FinalUrl += "#fragment" },
		func(s *runtimev1.BrowserSuccess) { s.Captures = []*runtimev1.CapturedValue{{CaptureId: "unexpected"}} },
	} {
		result := &runtimev1.BrowserResult{}
		_ = proto.Unmarshal(policyResult(t, "Engineer", 200), result)
		mutate(result.GetSuccess())
		payload, _ := proto.MarshalOptions{Deterministic: true}.Marshal(result)
		if _, _, err := validatedPolicyDocument(payload); err == nil {
			t.Fatal("invalid render output accepted for retry decision")
		}
	}
}
