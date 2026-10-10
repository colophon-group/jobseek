//go:build !densitybench

package main

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

// Explicit read-only public qualification. The container has the repository
// registry and pinned binary, with no production credentials, queues or writer.
func TestLightpandaPairedPublicRegistryQualification(t *testing.T) {
	path := os.Getenv("LIGHTPANDA_PAIRED_LIVE_REGISTRY")
	if path == "" {
		t.Skip("public registry qualification requires explicit opt-in")
	}
	binary := integrationBinary(t)
	f, err := os.Open(path)
	if err != nil {
		t.Fatal("public registry unavailable")
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil || len(rows) < 2 {
		t.Fatal("public registry invalid")
	}
	columns := map[string]int{}
	for i, name := range rows[0] {
		columns[name] = i
	}
	for _, name := range []string{"board_slug", "board_url", "monitor_type", "monitor_config"} {
		if _, ok := columns[name]; !ok {
			t.Fatal("registry missing column")
		}
	}
	counts := map[string]int{}
	httpBoards := 0
	for _, row := range rows[1:] {
		provider := row[columns["monitor_type"]]
		board, metadata := row[columns["board_url"]], row[columns["monitor_config"]]
		if provider != "accenture" && provider != "brassring" {
			continue
		}
		if strings.TrimSpace(metadata) == "" {
			metadata = "{}"
		}
		if provider == "accenture" {
			a, _, err := api.AccentureBrowserOptions(board, metadata)
			if err != nil {
				t.Fatal("registry factory invalid")
			}
			if a.Endpoint != api.AccentureFindJobs {
				t.Fatal("unqualified legacy Accenture route remains")
			}
			httpBoards++
			continue
		}
		counts[provider]++
		t.Run(row[columns["board_slug"]], func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
			defer cancel()
			hash := sha256.Sum256([]byte(board + "\n" + metadata))
			identity := hex.EncodeToString(hash[:])
			run := func(ctx context.Context, config Config, task Task) (Result, error) {
				result, err := runTask(ctx, config, task)
				if err != nil {
					var reserved *policy.Reservation
					var status *replayStatusError
					code := 0
					if errors.As(err, &status) {
						code = status.status
					}
					t.Logf("runner_failed deadline=%t cleanup_unproved=%t snapshot_changed=%t capture_failed=%t publisher_reserved=%t status=%d", errors.Is(err, context.DeadlineExceeded), errors.Is(err, errCleanupUnproved), errors.Is(err, api.ErrBrassRingSnapshot), errors.Is(err, errReplayCapture), errors.As(err, &reserved), code)
				}
				return result, err
			}
			execution := &runtimeV1ServiceExecution{dayforceConfig: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dayforceRun: run}
			response, err := execution.executeAPIReplay(ctx, replay.Request{Protocol: replay.Protocol, RequestID: identity, ConfigFingerprint: identity, BoardURL: board, Metadata: json.RawMessage(metadata), Provider: provider, TimeoutMS: 300000})
			if err != nil || response.Outcome != "success" {
				t.Fatalf("public provider outcome=%s", response.Outcome)
			}
			var inventory api.Inventory
			if json.Unmarshal(response.Inventory, &inventory) != nil {
				t.Fatal("public inventory invalid")
			}
			missing := 0
			for _, job := range inventory.Jobs {
				if len(job.Locations) == 0 {
					missing++
				}
			}
			if provider == "brassring" {
				if err = hydratePairedPublicBrassLocations(ctx, board, &inventory); err != nil {
					t.Fatal("public detail hydration failed")
				}
			}
			titles, descriptions, locations, dates := 0, 0, 0, 0
			for _, job := range inventory.Jobs {
				if title, ok := job.Title.(string); ok && strings.TrimSpace(title) != "" {
					titles++
				}
				if body, ok := job.Description.(string); ok && strings.TrimSpace(body) != "" {
					descriptions++
				}
				if len(job.Locations) > 0 {
					locations++
				}
				if date, ok := job.DatePosted.(string); ok && date != "" {
					dates++
				}
			}
			if titles != len(inventory.Jobs) || provider == "brassring" && locations != len(inventory.Jobs) {
				t.Fatal("public required fields missing")
			}
			body, err := json.Marshal(inventory)
			if err != nil {
				t.Fatal("public inventory fingerprint failed")
			}
			digest := sha256.Sum256(body)
			t.Logf("provider=%s jobs=%d truncated=%t frame_bytes=%d native_bytes=%d titles=%d descriptions=%d locations=%d dates=%d hydrated_missing=%d inventory_sha256=%s", provider, len(inventory.Jobs), inventory.Truncated, len(response.Inventory), len(body), titles, descriptions, locations, dates, missing, hex.EncodeToString(digest[:]))
		})
	}
	if httpBoards != 12 || counts["brassring"] != 4 {
		t.Fatal("paired public census changed", counts)
	}
}

func hydratePairedPublicBrassLocations(ctx context.Context, board string, inventory *api.Inventory) error {
	transport := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12}, MaxConnsPerHost: 8, MaxIdleConnsPerHost: 8}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 60 * time.Second, CheckRedirect: func(next *http.Request, prior []*http.Request) error {
		if len(prior) > 5 || !api.BrassRingDetailResourceMatches(board, next.URL.String()) {
			return errors.New("public detail redirect scope changed")
		}
		return nil
	}}
	call, cancel := context.WithCancel(ctx)
	defer cancel()
	var wg sync.WaitGroup
	semaphore := make(chan struct{}, 8)
	failures := make([]error, len(inventory.Jobs))
	for _, job := range inventory.Jobs {
		if len(job.Locations) > 0 {
			continue
		}
		if !api.BrassRingDetailResourceMatches(board, job.URL) || !strings.HasPrefix(job.URL, "https://sjobs.brassring.com/") && !strings.HasPrefix(job.URL, "https://xjobs.brassring.com/") {
			return errors.New("public detail origin changed")
		}
	}
	for i := range inventory.Jobs {
		job := &inventory.Jobs[i]
		if len(job.Locations) > 0 {
			continue
		}
		select {
		case semaphore <- struct{}{}:
		case <-call.Done():
			wg.Wait()
			return call.Err()
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			defer func() { <-semaphore }()
			failures[i] = func() error {
				id, ok := job.Metadata["requisition_id"].(string)
				if !ok {
					return errors.New("public requisition missing")
				}
				request, _ := http.NewRequestWithContext(call, "GET", job.URL, nil)
				response, err := client.Do(request)
				if err != nil {
					return errors.New("public detail request failed")
				}
				defer response.Body.Close()
				if response.StatusCode != 200 {
					return errors.New("public detail HTTP status failed")
				}
				reservation, policyURL := response.Header.Get("TDM-Reservation"), response.Header.Get("TDM-Policy")
				signals := &runtimev1.ResourcePolicySignals{TdmReservationHeader: &reservation, TdmPolicyHeader: &policyURL}
				if err = policy.Check(signals, "", response.Request.URL.String()); err != nil {
					return errors.New("public detail publisher reserved")
				}
				body, err := io.ReadAll(io.LimitReader(response.Body, (16<<20)+1))
				if err != nil || len(body) > 16<<20 {
					return errors.New("public detail body failed")
				}
				if err = policy.Check(signals, string(body), response.Request.URL.String()); err != nil {
					return errors.New("public detail publisher reserved")
				}
				job.Locations, err = api.BrassRingDetailLocation(string(body), id)
				return err
			}()
			if failures[i] != nil {
				cancel()
			}
		}()
	}
	wg.Wait()
	for _, err := range failures {
		if err != nil {
			return err
		}
	}
	return ctx.Err()
}
