package executor

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	"google.golang.org/protobuf/proto"
)

func executorFixture(t *testing.T) (*Executor, Request) {
	t.Helper()
	store, f, board := fixture(t)
	request, err := DecodeRequest(protocolFixture(t)["request"])
	if err != nil {
		t.Fatal(err)
	}
	if err := request.validateTask("lightpanda-b0", 7); err != nil {
		t.Fatal(err)
	}
	e := request.Task.Envelope
	e.TaskID, e.BoardID, e.SourceURL, e.Domain, e.RoutingEpoch = f.PostingID, board, "https://native-executor.invalid/"+f.PostingID, "native-executor.invalid", f.RoutingEpoch
	raw, err := b0task.CanonicalJSON(e, false)
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256(raw)
	request.TaskPayload, request.PayloadSHA256, request.ClaimToken = string(raw), hex.EncodeToString(hash[:]), fmt.Sprintf("%d:1", f.RoutingEpoch)
	if err := request.validateTask(f.ShardID, f.RoutingEpoch); err != nil {
		t.Fatal(err)
	}
	metadata, err := json.Marshal(map[string]any{"scraper_type": "json-ld", "scraper_config": e.ParserConfig})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.pool.Exec(context.Background(), "UPDATE job_board SET metadata=$2::jsonb,scraper_needs_browser=true,is_enabled=true,board_status='active' WHERE id=$1", board, metadata); err != nil {
		t.Fatal(err)
	}
	request.Result = renderedFixture(`<script type="application/ld+json">{"@type":"JobPosting","title":"Native Engineer","description":"<p>Python engineering work.</p>"}</script>`, e.SourceURL, 200)
	matcher, err := enrichment.Load("../../data")
	if err != nil {
		t.Fatal(err)
	}
	return &Executor{Store: store, Processor: &Processor{Matcher: matcher, Lookups: &NativeLookups{}, Locations: &preparationLocations{}}, Shard: f.ShardID, Epoch: f.RoutingEpoch}, request
}

func executorSocket(t *testing.T, executor *Executor) string {
	t.Helper()
	directory, err := os.MkdirTemp("/tmp", "jobseek-native-conversation-")
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "executor.sock")
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- (Server{Shard: executor.Shard, Epoch: executor.Epoch, Store: executor.Store, Task: executor.Handle}).serve(ctx, path, sameTestPeer)
	}()
	t.Cleanup(func() {
		cancel()
		select {
		case err := <-done:
			if err != nil {
				t.Error(err)
			}
		case <-time.After(3 * time.Second):
			t.Error("executor did not stop")
		}
		_ = os.RemoveAll(directory)
	})
	deadline := time.Now().Add(3 * time.Second)
	for privateSocket(path) != nil {
		if time.Now().After(deadline) {
			t.Fatal("executor socket unavailable")
		}
		time.Sleep(5 * time.Millisecond)
	}
	return path
}

func runExecutorConversation(t *testing.T, path string, request Request, authorize bool) map[string]json.RawMessage {
	t.Helper()
	conn := unixConversation(t, path)
	result, err := (proto.MarshalOptions{Deterministic: true}).Marshal(request.Result)
	if err != nil {
		t.Fatal(err)
	}
	if err := WriteMessage(conn, map[string]any{"version": Protocol, "task_payload": request.TaskPayload, "payload_sha256": request.PayloadSHA256, "claim_token": request.ClaimToken, "lease_until_ms": request.LeaseUntilMS, "browser_result": base64.StdEncoding.EncodeToString(result)}); err != nil {
		t.Fatal(err)
	}
	if readKind(t, conn) != "authorize" {
		t.Fatal("native task did not request authorization")
	}
	claim := request.ClaimToken
	if !authorize {
		claim = "wrong"
	}
	if err := WriteMessage(conn, map[string]any{"type": "authorized", "claim_token": claim, "lease_until_ms": request.LeaseUntilMS + 1}); err != nil {
		t.Fatal(err)
	}
	raw, err := ReadFrame(conn)
	if err != nil {
		t.Fatal(err)
	}
	var response map[string]json.RawMessage
	if json.Unmarshal(raw, &response) != nil {
		t.Fatal("invalid native response")
	}
	return response
}

func TestPostgresNativeConversationCommitsScheduleAndRejectsDuplicate(t *testing.T) {
	e, request := executorFixture(t)
	path := executorSocket(t, e)
	response := runExecutorConversation(t, path, request, true)
	if string(response["type"]) != `"committed"` {
		t.Fatalf("commit failed: %v", response)
	}
	var title []string
	var html string
	var uploaded bool
	var failures int
	var next time.Time
	if err := e.Store.pool.QueryRow(context.Background(), "SELECT jp.titles,jp.scrape_failures,jp.next_scrape_at,d.html,d.r2_uploaded FROM job_posting jp JOIN descriptions d ON d.posting_id=jp.id WHERE jp.id=$1", request.Task.Envelope.TaskID).Scan(&title, &failures, &next, &html, &uploaded); err != nil {
		t.Fatal(err)
	}
	if len(title) != 1 || title[0] != "Native Engineer" || failures != 0 || html != "<p>Python engineering work.</p>" || uploaded {
		t.Fatal("native canonical database effects differ")
	}
	var ready int64
	if json.Unmarshal(response["next_ready_at_ms"], &ready) != nil || ready != next.UnixMilli() {
		t.Fatal("acknowledgement differs from database schedule")
	}
	response = runExecutorConversation(t, path, request, true)
	if string(response["type"]) != `"authority_lost"` {
		t.Fatal("committed duplicate retained authority")
	}
	var after time.Time
	if err := e.Store.pool.QueryRow(context.Background(), "SELECT next_scrape_at FROM job_posting WHERE id=$1", request.Task.Envelope.TaskID).Scan(&after); err != nil || !after.Equal(next) {
		t.Fatal("duplicate changed schedule")
	}
}

func TestPostgresNativeConversationRequiresAuthorizationAndPreservesFailurePolicy(t *testing.T) {
	for _, status := range []uint32{200, 403, 404, 400} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			e, request := executorFixture(t)
			path := executorSocket(t, e)
			if status == 200 {
				response := runExecutorConversation(t, path, request, false)
				if string(response["type"]) != `"error"` {
					t.Fatal("invalid authorization accepted")
				}
				var count int
				if err := e.Store.pool.QueryRow(context.Background(), "SELECT count(*) FROM lightpanda_b0_write_fence WHERE job_posting_id=$1", request.Task.Envelope.TaskID).Scan(&count); err != nil || count != 0 {
					t.Fatal("unauthorized fence created")
				}
				return
			}
			request.Result = renderedFixture("", request.Task.Envelope.SourceURL, status)
			response := runExecutorConversation(t, path, request, true)
			if string(response["type"]) != `"committed"` {
				t.Fatal("failure schedule was not committed")
			}
			var title []string
			var active bool
			var failures int
			var next *time.Time
			if err := e.Store.pool.QueryRow(context.Background(), "SELECT titles,is_active,scrape_failures,next_scrape_at FROM job_posting WHERE id=$1", request.Task.Envelope.TaskID).Scan(&title, &active, &failures, &next); err != nil {
				t.Fatal(err)
			}
			if len(title) != 1 || title[0] != "Original title" {
				t.Fatal("HTTP failure clobbered content")
			}
			switch status {
			case 403:
				if !active || failures != 1 || next == nil {
					t.Fatal("transient retry counter or visibility changed")
				}
			case 404:
				if active || next != nil || response["next_ready_at_ms"] != nil {
					t.Fatal("gone did not clear active schedule")
				}
			case 400:
				if !active || failures != 1 || next == nil {
					t.Fatal("budget failure not counted once")
				}
			}
		})
	}
}

func TestPostgresNativeConversationSkipsReservedAndUnscheduledContent(t *testing.T) {
	for _, mode := range []string{"reserved", "unscheduled"} {
		t.Run(mode, func(t *testing.T) {
			e, request := executorFixture(t)
			query := "UPDATE job_posting SET tdm_reserved=true WHERE id=$1"
			if mode == "unscheduled" {
				query = "UPDATE job_posting SET next_scrape_at=NULL WHERE id=$1"
			}
			if _, err := e.Store.pool.Exec(context.Background(), query, request.Task.Envelope.TaskID); err != nil {
				t.Fatal(err)
			}
			request.Result = renderedFixture("", request.Task.Envelope.SourceURL, 404)
			response := runExecutorConversation(t, executorSocket(t, e), request, true)
			if string(response["type"]) != `"committed"` {
				t.Fatal("existing skip behavior changed")
			}
			var titles []string
			var failures, descriptions int
			var active bool
			if err := e.Store.pool.QueryRow(context.Background(), "SELECT titles,scrape_failures,is_active,(SELECT count(*) FROM descriptions WHERE posting_id=jp.id) FROM job_posting jp WHERE id=$1", request.Task.Envelope.TaskID).Scan(&titles, &failures, &active, &descriptions); err != nil {
				t.Fatal(err)
			}
			if !active || failures != 0 || descriptions != 0 || len(titles) != 1 || titles[0] != "Original title" {
				t.Fatal("reserved or unscheduled task changed content or visibility")
			}
			if response["next_ready_at_ms"] != nil {
				t.Fatal("reserved or unscheduled task reacquired a schedule")
			}
			var state string
			if err := e.Store.pool.QueryRow(context.Background(), "SELECT state FROM lightpanda_b0_write_fence WHERE job_posting_id=$1", request.Task.Envelope.TaskID).Scan(&state); err != nil || state != "revoked" {
				t.Fatal("skip retained active write authority")
			}
		})
	}
}

func TestPostgresNativeObservedReservationIsFencedAndStopsMiningSchedule(t *testing.T) {
	for _, mode := range []string{"header", "meta", "meta_opt_in", "corrupt_manifest", "missing_evidence"} {
		t.Run(mode, func(t *testing.T) {
			e, request := executorFixture(t)
			ctx := context.Background()
			var before time.Time
			if err := e.Store.pool.QueryRow(ctx, "SELECT next_scrape_at FROM job_posting WHERE id=$1", request.Task.Envelope.TaskID).Scan(&before); err != nil {
				t.Fatal(err)
			}
			html := `<script type="application/ld+json">{"@type":"JobPosting","title":"Policy Engineer","description":"<p>Engineering work.</p>"}</script>`
			status := uint32(200)
			if mode == "meta" {
				html = `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="meta-license">` + html
				status = 404
			}
			if mode == "meta_opt_in" {
				html = `<meta name="tdm-reservation" content="0">` + html
			}
			request.Result = renderedFixture(html, request.Task.Envelope.SourceURL, status)
			value, policy := "1", "header-license"
			request.Result.GetSuccess().ResourcePolicy.TdmReservationHeader = &value
			request.Result.GetSuccess().ResourcePolicy.TdmPolicyHeader = &policy
			if mode == "corrupt_manifest" {
				request.Result.GetSuccess().Html.TotalSha256 = strings.Repeat("0", 64)
			}
			if mode == "missing_evidence" {
				request.Result.GetSuccess().ResourcePolicy = nil
			}
			response := runExecutorConversation(t, executorSocket(t, e), request, true)
			if string(response["type"]) != `"committed"` {
				t.Fatal("policy conversation did not commit")
			}
			var reserved, active bool
			var failures, descriptions int
			var titles []string
			var next time.Time
			var evidence []byte
			if err := e.Store.pool.QueryRow(ctx, "SELECT tdm_reserved,is_active,scrape_failures,titles,next_scrape_at,tdm_reservation,(SELECT count(*) FROM descriptions WHERE posting_id=jp.id) FROM job_posting jp WHERE id=$1", request.Task.Envelope.TaskID).Scan(&reserved, &active, &failures, &titles, &next, &evidence, &descriptions); err != nil {
				t.Fatal(err)
			}
			if !active {
				t.Fatal("resource policy delisted posting")
			}
			switch mode {
			case "header", "meta":
				if !reserved || failures != 0 || descriptions != 0 || len(titles) != 1 || titles[0] != "Original title" || !next.Equal(before) || response["next_ready_at_ms"] != nil {
					t.Fatal("reservation changed retained facts/visibility or reacquired mining schedule")
				}
				var recorded map[string]any
				if json.Unmarshal(evidence, &recorded) != nil || recorded["url"] != request.Task.Envelope.SourceURL || recorded["source"] != mode {
					t.Fatal("reservation evidence differs")
				}
				expectedPolicy := "header-license"
				if mode == "meta" {
					expectedPolicy = "meta-license"
				}
				if recorded["policy_url"] != expectedPolicy {
					t.Fatal("effective resource policy URL differs")
				}
				if duplicate := runExecutorConversation(t, executorSocket(t, e), request, true); string(duplicate["type"]) != `"authority_lost"` {
					t.Fatal("reservation commit retained duplicate authority")
				}
			case "meta_opt_in":
				if reserved || failures != 0 || descriptions != 1 || len(titles) != 1 || titles[0] != "Policy Engineer" || response["next_ready_at_ms"] == nil {
					t.Fatal("available HTML opt-in did not override header")
				}
			default:
				if reserved || failures != 1 || descriptions != 0 || len(titles) != 1 || titles[0] != "Original title" {
					t.Fatal("untrusted/missing evidence became a reservation or content write")
				}
			}
		})
	}
}

type reservationRaceLocations struct{ reserve func(context.Context) error }

func (l *reservationRaceLocations) Resolve(ctx context.Context, _ []string, _, _ string) ([]int64, []string, error) {
	return nil, nil, l.reserve(ctx)
}
func TestPostgresReservationWinningDuringPreparationBlocksContentCommit(t *testing.T) {
	e, request := executorFixture(t)
	e.Processor.Locations = &reservationRaceLocations{reserve: func(ctx context.Context) error {
		_, err := e.Store.pool.Exec(ctx, "UPDATE job_posting SET tdm_reserved=true WHERE id=$1", request.Task.Envelope.TaskID)
		return err
	}}
	response := runExecutorConversation(t, executorSocket(t, e), request, true)
	if string(response["type"]) != `"committed"` || response["next_ready_at_ms"] != nil {
		t.Fatal("concurrent reservation retained mining schedule")
	}
	var titles []string
	var descriptions, failures int
	var state string
	if err := e.Store.pool.QueryRow(context.Background(), "SELECT jp.titles,jp.scrape_failures,(SELECT count(*) FROM descriptions WHERE posting_id=jp.id),f.state FROM job_posting jp JOIN lightpanda_b0_write_fence f ON f.job_posting_id=jp.id WHERE jp.id=$1", request.Task.Envelope.TaskID).Scan(&titles, &failures, &descriptions, &state); err != nil {
		t.Fatal(err)
	}
	if len(titles) != 1 || titles[0] != "Original title" || failures != 0 || descriptions != 0 || state != "revoked" {
		t.Fatal("content persistence bypassed reservation that won during preparation")
	}
}
