package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealNjoynLongClaimKeepsHeartbeatAndSettlesCanonicalInventory(t *testing.T) {
	board := "https://fixture.njoyn.com/corp/xweb/xweb.asp?clid=fixture&page=joblisting"
	f := privateRichPipelineFixtureURL(t, "njoyn", `{"scraper_type":"json-ld","page_wait_ms":0}`, board, queue.Browser, queue.Browser)
	ctx := context.Background()
	claim, err := f.a.Claim(ctx, queue.Browser)
	if err != nil || claim == nil {
		t.Fatal("real Njoyn claim unavailable", err)
	}
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, _ map[string]string) (RichDiscovery, error) {
		timer := time.NewTimer(150 * time.Millisecond)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return RichDiscovery{}, ctx.Err()
		case <-timer.C:
		}
		posting := strings.Replace(board, "page=joblisting", "page=JobDetails&jobid=fixture&brid=1", 1)
		return parseAPIReplayResponse(p, replay.Response{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: p.EffectiveConfigSHA256, Outcome: "success", Inventory: json.RawMessage(fmt.Sprintf(`{"Jobs":[{"url":%q}],"URLOnly":true,"Truncated":false}`, posting))})
	})
	client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Fatal("Njoyn escaped its rendered session") })
	var heartbeats atomic.Int64
	services := runtimeServices{
		heartbeat: func(ctx context.Context, claim *queue.Claim) error {
			err := f.a.Heartbeat(ctx, claim)
			if err == nil {
				heartbeats.Add(1)
			}
			return err
		},
		execute: func(ctx context.Context, claim *queue.Claim) (*GreenhouseClaimResult, error) {
			return RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
		},
	}
	c := fixtureLoopConfig()
	c.taskTimeout, c.njoynTaskTimeout, c.heartbeat = 50*time.Millisecond, 5*time.Second, 25*time.Millisecond
	result, err := runLeasedClaim(ctx, c, services, claim, newRuntimeMetrics(1, c.stallTimeout))
	if err != nil || result == nil || !result.Settled || heartbeats.Load() == 0 {
		t.Fatal("long Njoyn claim lost heartbeat or canonical settlement", err)
	}
	assertRichDeadlineAndLease(t, f, "njoyn", queue.Browser)
	var inserted int
	if err := f.pg.QueryRow(ctx, `SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid`, f.board, f.original).Scan(&inserted); err != nil || inserted != 1 {
		t.Fatal("long claim did not publish exactly its complete URL inventory", err)
	}
}
