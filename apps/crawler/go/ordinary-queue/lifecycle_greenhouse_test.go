package queue

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/redis/go-redis/v9"
)

// This adapter replays decisions captured from the actual Python processor.
// Real database effects and attempt/queue conservation are tested below.
type lifecycleOracleTx struct {
	pgx.Tx
	active, missing, counted, delisted int
	metadata                           map[string]any
}
type lifecycleOracleRow struct{ active, missing int }

func (r lifecycleOracleRow) Scan(dst ...any) error {
	*dst[0].(*int), *dst[1].(*int) = r.active, r.missing
	return nil
}

type lifecycleOracleRows struct {
	pgx.Rows
	remaining int
}

func (r *lifecycleOracleRows) Next() bool {
	if r.remaining == 0 {
		return false
	}
	r.remaining--
	return true
}
func (r *lifecycleOracleRows) Close()     {}
func (r *lifecycleOracleRows) Err() error { return nil }
func (tx *lifecycleOracleTx) QueryRow(_ context.Context, sql string, _ ...any) pgx.Row {
	if sql != lifecycleQuery("count") {
		panic("unexpected oracle query")
	}
	tx.counted++
	return lifecycleOracleRow{tx.active, tx.missing}
}
func (tx *lifecycleOracleTx) Query(_ context.Context, sql string, args ...any) (pgx.Rows, error) {
	if sql != lifecycleQuery("missing") || args[2] != 1 {
		panic("unexpected oracle delist")
	}
	tx.delisted++
	return &lifecycleOracleRows{remaining: tx.missing}, nil
}
func (tx *lifecycleOracleTx) Exec(_ context.Context, sql string, args ...any) (pgconn.CommandTag, error) {
	if sql != lifecycleQuery("metadata") {
		panic("unexpected oracle write")
	}
	var patch map[string]any
	decoder := json.NewDecoder(strings.NewReader(args[1].(string)))
	decoder.UseNumber()
	if err := decoder.Decode(&patch); err != nil {
		return pgconn.CommandTag{}, err
	}
	for key, value := range patch {
		tx.metadata[key] = value
	}
	return pgconn.NewCommandTag("UPDATE 1"), nil
}

func TestGreenhouseLifecyclePythonGuardOracle(t *testing.T) {
	body, err := os.ReadFile("testdata/python_lifecycle.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name                                                 string
		Metadata                                             map[string]any
		Active, Missing, Discovered, Gone, Counted, Delisted int
		Complete                                             bool
		Identities                                           []string
		Skipped                                              string
		ResultMetadata                                       map[string]any `json:"result_metadata"`
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if err := decoder.Decode(&cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) != 25 {
		t.Fatal("missing captured lifecycle cases")
	}
	for _, item := range cases {
		t.Run(item.Name, func(t *testing.T) {
			md := map[string]any{}
			for key, value := range item.Metadata {
				md[key] = value
			}
			tx := &lifecycleOracleTx{active: item.Active, missing: item.Missing, metadata: md}
			cycle := &GreenhouseCycle{claim: &Claim{task: Task{ID: "00000000-0000-4000-8000-000000000001"}}, identities: map[string]bool{}}
			for _, id := range item.Identities {
				cycle.identities[id] = true
			}
			gone, skipped, err := cycle.markGone(context.Background(), tx, item.Metadata, item.Discovered, item.Complete)
			actual, _ := json.Marshal(tx.metadata)
			expected, _ := json.Marshal(item.ResultMetadata)
			if err != nil || gone != item.Gone || skipped != item.Skipped || tx.counted != item.Counted || tx.delisted != item.Delisted || !bytes.Equal(actual, expected) {
				t.Fatalf("Python lifecycle mismatch: gone=%d skip=%s err=%v metadata=%s expected=%s", gone, skipped, err, actual, expected)
			}
		})
	}
}

func lifecycleFixture(t *testing.T, metadata string) (authorityFixture, *Authority) {
	t.Helper()
	f := greenhouseAuthorityFixture(t)
	ctx := context.Background()
	var projected string
	if err := f.observer.QueryRow(ctx, `UPDATE job_board SET metadata=metadata || $2::jsonb,
 last_non_empty_at=now()-interval '1 day' WHERE id=$1::uuid RETURNING metadata::text`, f.task.ID, metadata).Scan(&projected); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "metadata", projected).Err(); err != nil {
		t.Fatal(err)
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET last_seen_at=now()-interval '1 day' WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	p := stageFixturePlan(t, f, strings.Repeat("a", 40))
	activateFixturePlan(t, f, p)
	if err := f.client.redis.Set(ctx, ownershipProjectionKey, p.projection, 0).Err(); err != nil {
		t.Fatal(err)
	}
	a, err := OpenOwnedAuthority(ctx, f.dsn, f.client, f.epoch, p.digest, p.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Close)
	t.Cleanup(func() {
		if _, err := f.observer.Exec(ctx, "DELETE FROM job_posting WHERE company_id=$1::uuid AND id<>$2::uuid", f.company, f.task.ID); err != nil {
			t.Error(err)
		}
	})
	return f, a
}

func beginLifecycle(t *testing.T, a *Authority) *GreenhouseCycle {
	t.Helper()
	claim, err := a.Claim(context.Background(), Simple)
	if err != nil || claim == nil || claim.RecoveredReceipt() != nil {
		t.Fatalf("new lifecycle claim: %v", err)
	}
	cycle, err := a.BeginGreenhouseCycle(context.Background(), claim)
	if err != nil {
		t.Fatal(err)
	}
	return cycle
}

func settleLifecycle(t *testing.T, f authorityFixture, c *GreenhouseCycle, result *GreenhouseCycleResult) {
	t.Helper()
	ctx := context.Background()
	if result == nil || result.Receipt == nil {
		t.Fatal("terminal result has no durable receipt")
	}
	var due time.Time
	if err := f.observer.QueryRow(ctx, "SELECT next_check_at FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&due); err != nil {
		t.Fatal(err)
	}
	if err := c.authority.Settle(ctx, c.claim, result.Receipt); err != nil {
		t.Fatal(err)
	}
	score, err := f.client.redis.ZScore(ctx, "monitors_simple:greenhouse", f.task.ID).Result()
	if err != nil || score != seconds(due) || f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("lifecycle settlement lost canonical deadline or retained inflight work")
	}
}

func dueLifecycle(t *testing.T, f authorityFixture) {
	t.Helper()
	ctx := context.Background()
	// Only the owned private fixture advances time between complete cycles.
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET next_check_at=clock_timestamp()-interval '1 minute' WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	now, err := f.client.clock(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.ZAdd(ctx, "monitors_simple:greenhouse", redis.Z{Score: now - 1, Member: f.task.ID}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.ZAdd(ctx, "ready:simple:1", redis.Z{Score: now - 1, Member: "greenhouse"}).Err(); err != nil {
		t.Fatal(err)
	}
}

func TestRealOwnedLifecycleConfirmedDropUsesFreshCanonicalMetadata(t *testing.T) {
	f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture","recent_discovered_counts":[10,10,10]}`)
	ctx := context.Background()
	before, err := f.client.redis.HGetAll(ctx, "board:"+f.task.ID).Result()
	if err != nil {
		t.Fatal(err)
	}
	posting := richPosting(t, "https://job-boards.greenhouse.io/fixture/jobs/"+ordinaryID(t), "Present", "<p>Body</p>")
	for i := 1; i <= 3; i++ {
		if i > 1 {
			dueLifecycle(t, f)
		}
		c := beginLifecycle(t, a)
		if _, err := c.WriteRichBatch(ctx, []GreenhouseRichPosting{posting}); err != nil {
			t.Fatal(err)
		}
		result, err := c.FinishSuccess(ctx, GreenhouseInventorySummary{Discovered: 1})
		if err != nil {
			t.Fatal(err)
		}
		wantGone, wantSkip := 0, "drop"
		if i == 3 {
			wantGone, wantSkip = 1, ""
		}
		if result.Gone != wantGone || result.GoneSkipped != wantSkip {
			t.Fatalf("cycle %d ignored fresh PG candidate: %+v", i, result)
		}
		settleLifecycle(t, f, c, result)
		var active bool
		var streak int
		if err := f.observer.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.task.ID).Scan(&active); err != nil {
			t.Fatal(err)
		}
		if err := f.observer.QueryRow(ctx, "SELECT (metadata->>'suspect_streak')::integer FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&streak); err != nil {
			t.Fatal(err)
		}
		if active != (i < 3) || (i < 3 && streak != i) || (i == 3 && streak != 0) {
			t.Fatal("drop guard committed incorrect liveness/streak")
		}
	}
	after, err := f.client.redis.HGetAll(ctx, "board:"+f.task.ID).Result()
	if err != nil || !reflect.DeepEqual(before, after) {
		t.Fatal("cycle changed detached claim cache during settlement")
	}
}

func TestRealOwnedLifecycleRepeatedEmptyAndRecovery(t *testing.T) {
	f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture","_confirmed_drop_candidate":{"confirmations":2}}`)
	ctx := context.Background()
	for i := 1; i <= 6; i++ {
		if i > 1 {
			dueLifecycle(t, f)
		}
		c := beginLifecycle(t, a)
		result, err := c.FinishSuccess(ctx, GreenhouseInventorySummary{Discovered: 2, ProcessingFiltered: 2, Truncated: true})
		if err != nil {
			t.Fatal(err)
		}
		wantGone := 0
		if i == 6 {
			wantGone = 1
		}
		if result.Gone != wantGone {
			t.Fatal("empty result removed jobs before sixth confirmation")
		}
		settleLifecycle(t, f, c, result)
		var status string
		var count int
		var candidateCleared bool
		if err := f.observer.QueryRow(ctx, "SELECT board_status,empty_check_count,metadata->'_confirmed_drop_candidate'='null'::jsonb FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&status, &count, &candidateCleared); err != nil {
			t.Fatal(err)
		}
		wantStatus := "active"
		if i >= 3 {
			wantStatus = "suspect"
		}
		if status != wantStatus || count != i || !candidateCleared {
			t.Fatal("repeated empty lifecycle state mismatch")
		}
	}
	dueLifecycle(t, f)
	c := beginLifecycle(t, a)
	url := "https://job-boards.greenhouse.io/fixture/jobs/" + ordinaryID(t)
	if _, err := c.WriteRichBatch(ctx, []GreenhouseRichPosting{richPosting(t, url, "Recovered", "<p>Recovered</p>")}); err != nil {
		t.Fatal(err)
	}
	result, err := c.FinishSuccess(ctx, GreenhouseInventorySummary{Discovered: 1})
	if err != nil {
		t.Fatal(err)
	}
	settleLifecycle(t, f, c, result)
	var status string
	var empty int
	if err := f.observer.QueryRow(ctx, "SELECT board_status,empty_check_count FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&status, &empty); err != nil || status != "active" || empty != 0 {
		t.Fatal("suspect board stranded after nonempty recovery")
	}
}

func TestRealOwnedLifecycleFailureQuarantineAndNonemptyRecovery(t *testing.T) {
	f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture"}`)
	ctx := context.Background()
	for i := 1; i <= 10; i++ {
		if i > 1 {
			dueLifecycle(t, f)
		}
		c := beginLifecycle(t, a)
		result, err := c.FinishFailure(ctx, "bounded provider failure")
		if err != nil {
			t.Fatal(err)
		}
		if result.EnteredQuarantine != (i == 5) {
			t.Fatal("incorrect quarantine transition")
		}
		var status string
		var strikes int
		var minutes float64
		var active bool
		if err := f.observer.QueryRow(ctx, "SELECT board_status,consecutive_failures,extract(epoch FROM next_check_at-updated_at)/60 FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&status, &strikes, &minutes); err != nil {
			t.Fatal(err)
		}
		want := float64(5 * (int64(1) << uint(i-1)))
		if want > 1440 {
			want = 1440
		}
		if strikes != i || minutes != want || (i >= 5 && status != "quarantined") {
			t.Fatal("failure budget or capped canonical backoff changed")
		}
		if err := f.observer.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.task.ID).Scan(&active); err != nil || !active {
			t.Fatal("failed cycle removed unseen posting")
		}
		settleLifecycle(t, f, c, result)
	}
	dueLifecycle(t, f)
	c := beginLifecycle(t, a)
	posting := richPosting(t, "https://job-boards.greenhouse.io/fixture/jobs/"+ordinaryID(t), "Recovered", "<p>Body</p>")
	if _, err := c.WriteRichBatch(ctx, []GreenhouseRichPosting{posting}); err != nil {
		t.Fatal(err)
	}
	result, err := c.FinishSuccess(ctx, GreenhouseInventorySummary{Discovered: 1})
	if err != nil || result.RecoveredFrom == nil || *result.RecoveredFrom != "quarantined" {
		t.Fatalf("quarantine recovery failed: %v", err)
	}
	settleLifecycle(t, f, c, result)
}

func TestRealOwnedLifecyclePartialPoisonReservationAndInvalidCompletion(t *testing.T) {
	for _, mode := range []string{"truncated", "filtered", "failed_batch", "reserved", "cancelled", "stale_config", "disabled", "invalid_summary"} {
		t.Run(mode, func(t *testing.T) {
			f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture","recent_discovered_counts":[10,10,10]}`)
			ctx := context.Background()
			c := beginLifecycle(t, a)
			if _, err := a.BeginGreenhouseCycle(ctx, c.claim); !errors.Is(err, ErrConfiguration) {
				t.Fatal("one claim admitted multiple inventory starts")
			}
			posting := richPosting(t, "https://job-boards.greenhouse.io/fixture/jobs/"+ordinaryID(t), "Present", "<p>Body</p>")
			if _, err := c.WriteRichBatch(ctx, []GreenhouseRichPosting{posting}); err != nil {
				t.Fatal(err)
			}
			input := GreenhouseInventorySummary{Discovered: 1}
			finishCtx := ctx
			switch mode {
			case "truncated":
				input.Truncated = true
			case "filtered":
				input.Discovered = 2
				input.ProcessingFiltered = 1
			case "failed_batch":
				posting.Content.Description.Hash++
				if _, err := c.WriteRichBatch(ctx, []GreenhouseRichPosting{posting}); err == nil {
					t.Fatal("invalid batch succeeded")
				}
			case "reserved":
				if _, err := f.observer.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", f.task.ID); err != nil {
					t.Fatal(err)
				}
			case "cancelled":
				var cancel context.CancelFunc
				finishCtx, cancel = context.WithCancel(ctx)
				cancel()
			case "stale_config":
				if _, err := f.observer.Exec(ctx, `UPDATE job_board SET metadata=metadata || '{"_monitor_config_fingerprint":"changed"}' WHERE id=$1::uuid`, f.task.ID); err != nil {
					t.Fatal(err)
				}
			case "disabled":
				if _, err := f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", f.task.ID); err != nil {
					t.Fatal(err)
				}
			case "invalid_summary":
				input.Discovered = 0
			}
			result, err := c.FinishSuccess(finishCtx, input)
			if mode == "truncated" || mode == "filtered" {
				if err != nil || result.Gone != 0 || result.GoneSkipped == "" {
					t.Fatalf("partial inventory applied absence: %v", err)
				}
				settleLifecycle(t, f, c, result)
				if _, err := c.FinishSuccess(ctx, input); !errors.Is(err, ErrConfiguration) {
					t.Fatal("terminal cycle was reusable")
				}
			} else {
				if err == nil || result != nil {
					t.Fatal("unsafe completion issued receipt")
				}
				var state string
				if err := f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.task.ID).Scan(&state); err != nil || state != "active" {
					t.Fatal("failed completion changed attempt state")
				}
			}
			var active bool
			if err := f.observer.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.task.ID).Scan(&active); err != nil || !active {
				t.Fatal("partial or rejected completion removed unseen posting")
			}
		})
	}
}

func TestRealOwnedLifecycleFinalizationRollbackRetainsCommittedBatch(t *testing.T) {
	f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture"}`)
	ctx := context.Background()
	c := beginLifecycle(t, a)
	posting := richPosting(t, "https://job-boards.greenhouse.io/fixture/jobs/"+ordinaryID(t), "Committed batch", "<p>Committed batch</p>")
	if _, err := c.WriteRichBatch(ctx, []GreenhouseRichPosting{posting}); err != nil {
		t.Fatal(err)
	}
	name := "ordinary_lifecycle_" + strings.ReplaceAll(ordinaryID(t), "-", "")
	schema := pgx.Identifier{name}.Sanitize()
	function := pgx.Identifier{name, "inject"}.Sanitize()
	trigger := pgx.Identifier{name}.Sanitize()
	if _, err := f.observer.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.observer.Exec(ctx, "DROP TRIGGER IF EXISTS "+trigger+" ON job_board")
		_, _ = f.observer.Exec(ctx, "DROP SCHEMA "+schema+" CASCADE")
	})
	var quotedID string
	if err := f.observer.QueryRow(ctx, "SELECT quote_literal($1::text)", f.task.ID).Scan(&quotedID); err != nil {
		t.Fatal(err)
	}
	sql := fmt.Sprintf(`CREATE FUNCTION %s() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN IF NEW.id::text=%s AND NEW.last_success_at IS DISTINCT FROM OLD.last_success_at THEN
 RAISE EXCEPTION USING ERRCODE='XX000',MESSAGE='private terminal rollback fixture';
 END IF; RETURN NEW; END $$;
 CREATE TRIGGER %s BEFORE UPDATE ON job_board FOR EACH ROW EXECUTE FUNCTION %s()`, function, quotedID, trigger, function)
	if _, err := f.observer.Exec(ctx, sql); err != nil {
		t.Fatal(err)
	}
	result, err := c.FinishSuccess(ctx, GreenhouseInventorySummary{Discovered: 1})
	var failure *pgconn.PgError
	if result != nil || !errors.As(err, &failure) || failure.Code != "XX000" {
		t.Fatal("terminal SQL failure issued durable receipt")
	}
	var active, historyAbsent bool
	var due time.Time
	var state string
	var count int
	if err := f.observer.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.task.ID).Scan(&active); err != nil || !active {
		t.Fatal("failed terminal SQL left absence effects")
	}
	if err := f.observer.QueryRow(ctx, "SELECT NOT(metadata ? 'recent_discovered_counts'),next_check_at FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&historyAbsent, &due); err != nil || !historyAbsent || !due.Before(time.Now()) {
		t.Fatal("terminal rollback left lifecycle metadata/deadline")
	}
	if err := f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.task.ID).Scan(&state); err != nil || state != "active" {
		t.Fatal("terminal rollback completed fence")
	}
	if err := f.observer.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE source_url=$1 AND is_active", posting.URL).Scan(&count); err != nil || count != 1 {
		t.Fatal("terminal rollback lost prior committed chunk")
	}
	if _, err := f.observer.Exec(ctx, "DROP TRIGGER "+trigger+" ON job_board"); err != nil {
		t.Fatal(err)
	}
	result, err = c.FinishFailure(ctx, "bounded lifecycle persistence failure")
	if err != nil {
		t.Fatal(err)
	}
	settleLifecycle(t, f, c, result)
}

func TestRealOwnedLifecycleCrashAfterCommitRecoversChangedStatus(t *testing.T) {
	for _, mode := range []string{"gone_pending", "quarantined"} {
		t.Run(mode, func(t *testing.T) {
			f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture"}`)
			ctx := context.Background()
			if mode == "quarantined" {
				if _, err := f.observer.Exec(ctx, "UPDATE job_board SET consecutive_failures=4 WHERE id=$1::uuid", f.task.ID); err != nil {
					t.Fatal(err)
				}
			}
			c := beginLifecycle(t, a)
			var result *GreenhouseCycleResult
			var err error
			if mode == "gone_pending" {
				result, err = c.FinishProviderGone(ctx, GreenhouseGoneObservation{"https://boards-api.greenhouse.io/v1/boards/fixture/jobs?content=true", 404})
			} else {
				result, err = c.FinishFailure(ctx, "bounded provider failure")
			}
			if err != nil {
				t.Fatal(err)
			}
			due := result.Receipt.NextDue()
			plan := a.ownership
			// Process loss follows a real terminal PostgreSQL commit, before ack.
			a.Close()
			expire(t, f.client, &c.claim.task)
			if _, err := guardedReap(t, f); err != nil {
				t.Fatal(err)
			}
			replacement, err := OpenOwnedAuthority(ctx, f.dsn, f.client, f.epoch, plan.digest, plan.SourceRevision())
			if err != nil {
				t.Fatal(err)
			}
			defer replacement.Close()
			claim, err := replacement.Claim(ctx, Simple)
			if err != nil || claim == nil || claim.RecoveredReceipt() == nil || !claim.RecoveredReceipt().NextDue().Equal(*due) {
				t.Fatalf("recoverable lifecycle commit stranded: %v", err)
			}
			if _, err := replacement.BeginGreenhouseCycle(ctx, claim); !errors.Is(err, ErrConfiguration) {
				t.Fatal("recovered lifecycle attempt admitted network cycle")
			}
			if err := replacement.Settle(ctx, claim, claim.RecoveredReceipt()); err != nil {
				t.Fatal(err)
			}
			score, err := f.client.redis.ZScore(ctx, "monitors_simple:greenhouse", f.task.ID).Result()
			if err != nil || score != seconds(*due) {
				t.Fatal("crash recovery changed canonical schedule")
			}
		})
	}
}
