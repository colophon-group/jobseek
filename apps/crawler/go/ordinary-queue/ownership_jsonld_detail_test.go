package queue

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

func TestRealJSONLDDetailCursorUsesBoundedUUIDIndexTraversal(t *testing.T) {
	p := firstJSONLDDetailFixture(t)
	ctx := context.Background()
	tx, err := p.f.observer.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	// A tiny fixture may prefer a single-column index plus a one-row sort.
	// Discourage both sorting and sequential scans to prove the actual query
	// can use the migrated UUID keyset directly. The text alias cannot do so.
	if _, err := tx.Exec(ctx, "SET LOCAL enable_seqscan=off"); err != nil {
		t.Fatal(err)
	}
	if _, err := tx.Exec(ctx, "SET LOCAL enable_sort=off"); err != nil {
		t.Fatal(err)
	}
	var body []byte
	if err := tx.QueryRow(ctx, "EXPLAIN (FORMAT JSON) "+detailPostingCursorQuery, p.plan.document.Details[0].BoardID, "00000000-0000-0000-0000-000000000000").Scan(&body); err != nil {
		t.Fatal(err)
	}
	type node struct {
		Kind  string `json:"Node Type"`
		Index string `json:"Index Name"`
		Plans []node
	}
	var report []struct{ Plan node }
	if err := json.Unmarshal(body, &report); err != nil || len(report) != 1 {
		t.Fatal("invalid PostgreSQL cursor plan", err)
	}
	root := report[0].Plan
	if root.Kind != "Limit" || len(root.Plans) != 1 || root.Plans[0].Kind != "Index Scan" || root.Plans[0].Index != "idx_jp_board_id_cursor" {
		t.Fatalf("native detail cursor must traverse the UUID index directly, without a full-board sort: %s", body)
	}
}

// A large cohort can put all priority detail work outside the current
// eight-board window, preventing both detail and recurring monitor claims.
func TestRealJSONLDFirstTimeCandidateOutsideBoardWindow(t *testing.T) {
	p := firstJSONLDDetailFixture(t)
	ctx := context.Background()
	boards := []string{p.plan.document.Details[0].BoardID}
	for i := 0; i < 8; i++ {
		board := fmt.Sprintf("00000000-0000-4000-8000-%012d", 100+i)
		config := jsonldDetailConfig()
		config["company_id"], config["board_slug"] = p.f.company, "first-time-window-"+board
		config["board_url"] += "?board=" + board
		if _, err := p.f.observer.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser) VALUES($1::uuid,$2::uuid,$3,$4,'dom',$5::jsonb,60,24,'careers.example.com',true,false)`, board, p.f.company, config["board_slug"], config["board_url"], config["metadata"]); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() {
			_, _ = p.f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", board)
		})
		if err := p.f.client.redis.HSet(ctx, "board:"+board, config).Err(); err != nil {
			t.Fatal(err)
		}
		boards = append(boards, board)
	}
	plan, err := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.plan.document.Members[0].BoardID}, boards)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	activateFixturePlan(t, p.f, plan)
	if err := p.f.client.redis.Set(ctx, ownershipProjectionKey, plan.projection, 0).Err(); err != nil {
		t.Fatal(err)
	}
	a, err := OpenOwnedAuthority(ctx, p.f.dsn, p.f.client, p.f.epoch, plan.digest, plan.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Close)
	if plan.document.Details[8].BoardID != boards[0] {
		t.Fatal("due board must be outside initial scan window")
	}
	claim, err := a.Claim(ctx, Simple)
	if err != nil || claim == nil || claim.task.ID != p.f.task.ID || claim.task.Kind != Scrape {
		t.Fatal("first-time detail stranded outside cohort window", err)
	}
	if p.f.client.redis.ZCard(ctx, "inflight:simple").Val() != 1 {
		t.Fatal("candidate observation changed another lease")
	}
}

func firstJSONLDDetailFixture(t *testing.T) firstOwnerFixture {
	return firstIndependentDetailFixture(t, `{"scraper_type":"json-ld","selector":"a.job","render":true}`, "https://jobs.example.net/job/native-jsonld", "jobs.example.net")
}

func firstIndependentDetailFixture(t *testing.T, metadata, source, domain string, workers ...WorkerType) firstOwnerFixture {
	t.Helper()
	worker := Simple
	if len(workers) == 1 {
		worker = workers[0]
	}
	p := firstOwnershipFixture(t)
	f, ctx := p.f, context.Background()
	board := ordinaryID(t)
	if _, err := f.observer.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser) VALUES($1::uuid,$2::uuid,$3,'https://careers.example.com/jobs','dom',$4::jsonb,60,24,'careers.example.com',true,$5)`, board, f.company, "jsonld-"+board, metadata, worker == Browser); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", board) })
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET board_id=$2::uuid,source_url=$3,next_scrape_at=now()-interval '1 minute' WHERE id=$1::uuid", f.task.ID, board, source); err != nil {
		t.Fatal(err)
	}
	config := jsonldDetailConfig()
	config["company_id"], config["board_slug"], config["metadata"] = f.company, "jsonld-"+board, metadata
	if worker == Browser {
		config["scraper_needs_browser"] = "1"
	}
	if err := f.client.redis.HSet(ctx, "board:"+board, config).Err(); err != nil {
		t.Fatal(err)
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET next_check_at=now()+interval '1 hour' WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.Del(ctx, "monitors_simple:greenhouse", "ft_monitors_simple:greenhouse").Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.ZRem(ctx, "ready:simple:1", "greenhouse").Err(); err != nil {
		t.Fatal(err)
	}
	if _, err := f.client.EnqueueURLDetail(ctx, URLOnlyDetail{ID: f.task.ID, BoardID: board, URL: source, Due: time.Now().Add(-time.Minute), Browser: worker == Browser}); err != nil {
		t.Fatal(err)
	}
	plan, err := f.authority.StageOwnership(ctx, strings.Repeat("a", 40), []string{f.task.ID}, []string{board})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	p.plan = plan
	p.f.task = &Task{ID: f.task.ID, Kind: Scrape, Worker: worker, Domain: domain}
	return p
}

func TestRealJSONLDDetailOwnsPostingAndRetainsLegacyMonitor(t *testing.T) {
	testIndependentDetailOwnsPosting(t, firstJSONLDDetailFixture(t), jsonldDetailProfile, "jobs.example.net")
}

func testIndependentDetailOwnsPosting(t *testing.T, p firstOwnerFixture, profile, domain string) {
	a, claim := firstRetirementClaim(t, p)
	ctx := context.Background()
	board := claim.boardID
	if board == claim.task.ID || claim.task.Kind != Scrape {
		t.Fatal("actual posting/board identity lost")
	}
	if _, ok := map[string]string{p.plan.document.Members[0].BoardID: p.plan.document.Members[0].Domain}[board]; ok {
		t.Fatal("legacy monitor became native")
	}
	if err := p.f.client.redis.ZAdd(ctx, "monitors_browser:careers.example.com", redis.Z{Score: 1, Member: board}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.ZAdd(ctx, "ready:browser:1", redis.Z{Score: 1, Member: "careers.example.com"}).Err(); err != nil {
		t.Fatal(err)
	}
	legacy, err := p.f.client.ClaimLegacyBound(ctx, Browser, p.plan)
	if err != nil || legacy == nil || legacy.Kind != Monitor || legacy.ID != board {
		t.Fatal("independent detail ownership stranded legacy monitor", err)
	}
	if legacy, err := p.f.client.ClaimLegacyBound(ctx, Simple, p.plan); err != nil || legacy != nil {
		t.Fatal("legacy claimed owned detail", err)
	}
	detail, err := a.ReadWorkdayDetail(ctx, claim)
	if err != nil || detail.Profile().Profile != profile || detail.Profile().Domain != domain {
		t.Fatal("canonical generic detail missing", err)
	}
	receipt, err := a.WriteWorkdayDetail(ctx, detail, func(ctx context.Context, tx pgx.Tx) error {
		_, err := tx.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Native JSON-LD'],next_scrape_at=now()+interval '24 hours' WHERE id=$1::uuid", claim.task.ID)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := a.Settle(ctx, claim, receipt); err != nil {
		t.Fatal(err)
	}
	score, err := p.f.client.redis.ZScore(ctx, "scrapes_simple:"+domain, claim.task.ID).Result()
	if err != nil || score != seconds(*receipt.NextDue()) {
		t.Fatal("canonical JSON-LD deadline differs", err)
	}
}

func TestRealJSONLDDetailColdRetirementPreservesActualHostAndCanonicalDeadline(t *testing.T) {
	testIndependentDetailColdRetirement(t, firstJSONLDDetailFixture)
}

func testIndependentDetailColdRetirement(t *testing.T, fixture func(*testing.T) firstOwnerFixture) {
	for _, mode := range []string{"active", "committed-before-ack", "claim-before-sql", "inactive"} {
		t.Run(mode, func(t *testing.T) {
			p := fixture(t)
			a, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			switch mode {
			case "committed-before-ack":
				detail, err := a.ReadWorkdayDetail(ctx, claim)
				if err != nil {
					t.Fatal(err)
				}
				if _, err := a.WriteWorkdayDetail(ctx, detail, func(ctx context.Context, tx pgx.Tx) error {
					_, err := tx.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Native JSON-LD'],next_scrape_at=now()+interval '24 hours' WHERE id=$1::uuid", claim.task.ID)
					return err
				}); err != nil {
					t.Fatal(err)
				}
			case "claim-before-sql":
				if _, err := p.f.observer.Exec(ctx, "DELETE FROM ordinary_worker_write_fence WHERE task_kind='scrape' AND task_id=$1::uuid", claim.task.ID); err != nil {
					t.Fatal(err)
				}
			case "inactive":
				if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET is_active=false,next_scrape_at=NULL WHERE id=$1::uuid", claim.task.ID); err != nil {
					t.Fatal(err)
				}
			}
			var due *time.Time
			if err := p.f.observer.QueryRow(ctx, "SELECT next_scrape_at FROM job_posting WHERE id=$1::uuid", claim.task.ID).Scan(&due); err != nil {
				t.Fatal(err)
			}
			if _, err := applyFirstFixture(t, p, true); err != nil {
				t.Fatal("JSON-LD cold retirement failed", err)
			}
			if firstFixtureState(t, p) != "retired" || p.f.client.redis.Exists(ctx, ownershipProjectionKey).Val() != 0 || p.f.client.redis.ZCard(ctx, "inflight:"+string(claim.task.Worker)).Val() != 0 {
				t.Fatal("retirement retained owner/lease")
			}
			score, err := p.f.client.redis.ZScore(ctx, "scrapes_"+string(claim.task.Worker)+":"+claim.task.Domain, claim.task.ID).Result()
			if due == nil {
				if !errors.Is(err, redis.Nil) {
					t.Fatal("inactive detail requeued", err)
				}
			} else if err != nil || score != seconds(*due) {
				t.Fatal("actual-host deadline lost", err)
			}
			if _, err := a.WriteWorkdayDetail(ctx, &CurrentWorkdayDetail{claim: claim}, func(context.Context, pgx.Tx) error { t.Fatal("retired attempt wrote"); return nil }); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("retired JSON-LD attempt retained authority", err)
			}
		})
	}
}

func TestRealJSONLDDetailStagedInspectionRechecksIndependentConfiguration(t *testing.T) {
	p := firstJSONLDDetailFixture(t)
	ctx := context.Background()
	if _, err := p.f.authority.InspectStagedOwnership(ctx, p.plan.digest, p.plan.SourceRevision()); err != nil {
		t.Fatal(err)
	}
	board := p.plan.document.Details[0].BoardID
	if _, err := p.f.observer.Exec(ctx, `UPDATE job_board SET metadata=jsonb_set(metadata,'{scraper_config}','{"proxy":true}'::jsonb) WHERE id=$1::uuid`, board); err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.authority.InspectStagedOwnership(ctx, p.plan.digest, p.plan.SourceRevision()); !errors.Is(err, ErrUnsupportedProfile) {
		t.Fatal("changed independent detail context passed staged inspection", err)
	}
	if firstFixtureState(t, p) != "staged" || p.f.client.redis.Exists(ctx, ownershipProjectionKey).Val() != 0 {
		t.Fatal("inspection mutated ownership")
	}
}

func TestRealJSONLDDetailSelectionRotatesPastFullCandidateBoard(t *testing.T) {
	p := firstJSONLDDetailFixture(t)
	ctx := context.Background()
	second := ordinaryID(t)
	config := jsonldDetailConfig()
	config["company_id"], config["board_slug"] = p.f.company, "jsonld-"+second
	config["board_url"] += "?board=" + second
	if _, err := p.f.observer.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser) VALUES($1::uuid,$2::uuid,$3,$4,'dom',$5::jsonb,60,24,'careers.example.com',true,false)`, second, p.f.company, config["board_slug"], config["board_url"], config["metadata"]); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE board_id=$1::uuid", second)
		_, _ = p.f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", second)
	})
	if err := p.f.client.redis.HSet(ctx, "board:"+second, config).Err(); err != nil {
		t.Fatal(err)
	}
	plan, err := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.plan.document.Members[0].BoardID}, []string{p.plan.document.Details[0].BoardID, second})
	if err != nil {
		t.Fatal(err)
	}
	// Bounded observations alone cannot grant an owner. The private staged
	// fixture tests traversal without publishing any active projection or popping.
	a := p.f.authority
	a.ownership = plan
	first, last := plan.document.Details[0].BoardID, plan.document.Details[1].BoardID
	var inserted []string
	t.Cleanup(func() {
		for _, id := range inserted {
			_, _ = p.f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=$1::uuid", id)
		}
	})
	for index, board := range []string{first, last} {
		n := 65
		if index == 1 {
			n = 1
		}
		for i := 0; i < n; i++ {
			id := ordinaryID(t)
			inserted = append(inserted, id)
			source := "https://jobs.example.net/job/" + id
			if _, err := p.f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales,next_scrape_at) VALUES($1::uuid,$2::uuid,$3::uuid,$4,ARRAY['Native fixture'],ARRAY['en'],now()-interval '1 minute')`, id, p.f.company, board, source); err != nil {
				t.Fatal(err)
			}
			if _, err := p.f.client.EnqueueURLDetail(ctx, URLOnlyDetail{ID: id, BoardID: board, URL: source, Due: time.Now().Add(-time.Minute)}); err != nil {
				t.Fatal(err)
			}
		}
	}
	for pass := 0; pass < 2; pass++ {
		var candidates []ownershipCandidate
		err := a.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
			var e error
			candidates, e = a.detailCandidates(ctx, tx, seconds(time.Now()), Simple)
			return e
		})
		if err != nil {
			t.Fatal(err)
		}
		if pass == 0 && len(candidates) != ownershipCandidateBatch {
			t.Fatal("full board fixture did not fill batch", len(candidates))
		}
		if pass == 1 {
			found := false
			for _, c := range candidates {
				found = found || c.member.BoardID == last
			}
			if !found {
				t.Fatal("full candidate board starved the next detail board")
			}
		}
	}
}
