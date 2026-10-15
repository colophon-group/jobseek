package worker

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/redis/go-redis/v9"
)

func TestRealFirstOwnershipExecutableRepairsAndRetiresCanonicalDetail(t *testing.T) {
	testFirstOwnershipExecutableProjection(t, false)
}

func TestRealFirstOwnershipExecutableRestoresMissingScheduleAndRetires(t *testing.T) {
	testFirstOwnershipExecutableProjection(t, true)
}

func testFirstOwnershipExecutableProjection(t *testing.T, missing bool) {
	f, executable, _ := firstExecutableOwnershipFixture(t)
	ctx := context.Background()
	board, source := fixtureID(t), "https://example.com/job/cold-repair"
	metadata := `{"scraper_type":"json-ld","selector":"a.job","render":true}`
	if _, err := f.pg.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser)
 VALUES($1::uuid,$2::uuid,$3,'https://careers.example.net/jobs','dom',$4::jsonb,60,24,'careers.example.net',true,false)`, board, f.company, "projection-"+board, metadata); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = f.pg.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", board) })
	var due time.Time
	if err := f.pg.QueryRow(ctx, "UPDATE job_posting SET board_id=$2::uuid,source_url=$3,next_scrape_at=now()-interval '1 minute' WHERE id=$1::uuid RETURNING next_scrape_at", f.original, board, source).Scan(&due); err != nil {
		t.Fatal(err)
	}
	config := map[string]string{"board_slug": "projection-" + board, "company_id": f.company, "board_url": "https://careers.example.net/jobs", "crawler_type": "dom", "metadata": metadata, "domain": "careers.example.net", "throttle_key": "careers.example.net", "monitor_needs_browser": "1", "scraper_needs_browser": "0", "check_interval_minutes": "60", "scrape_interval_hours": "24"}
	if err := f.r.HSet(ctx, "board:"+board, config).Err(); err != nil {
		t.Fatal(err)
	}
	if _, err := f.client.EnqueueURLDetail(ctx, queue.URLOnlyDetail{ID: f.original, BoardID: f.board, URL: source, Due: due, Browser: true}); err != nil {
		t.Fatal(err)
	}
	if missing {
		f.r.Del(ctx, "scrape:"+f.original)
		for _, lane := range []string{"simple", "browser"} {
			for _, prefix := range []string{"ft_scrapes_", "scrapes_"} {
				if err := f.r.ZRem(ctx, prefix+lane+":example.com", f.original).Err(); err != nil {
					t.Fatal(err)
				}
			}
		}
	}
	if _, err := f.pg.Exec(ctx, "UPDATE job_board SET next_check_at=now()+interval '1 hour' WHERE id=$1::uuid", f.board); err != nil {
		t.Fatal(err)
	}
	f.r.Del(ctx, "ft_monitors_simple:greenhouse", "monitors_simple:greenhouse")
	plan, err := f.a.StageOwnership(ctx, ordinaryFixtureSourceRevision(t), []string{f.board}, []string{board})
	if err != nil {
		t.Fatal(err)
	}
	request := FirstOwnershipRequest{Version: "jobseek.ordinary.first-owner-request/v1", Operation: "activate", SourceRevision: plan.SourceRevision(), RoutingEpoch: plan.Epoch(), PlanSHA256: plan.SHA256(), ProjectionSHA1: plan.ProjectionSHA1(), CrawlerImageRef: "ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("d", 64), B0ReceiptSHA256: strings.Repeat("e", 64), B0Cohort: "c1", Namespace: "production-b0", ShardID: "lightpanda-b0", ColdHostSHA256: strings.Repeat("f", 64)}
	path := filepath.Join(executable.directory, "detail-projection-owner.json")
	call := func(accepted bool) {
		t.Helper()
		body, _ := json.Marshal(request)
		if err := os.WriteFile(path, append(body, '\n'), 0o600); err != nil {
			t.Fatal(err)
		}
		command := exec.Command(executable.binary, "--"+request.Operation+"-first-ownership")
		command.Env = []string{"PATH=" + os.Getenv("PATH"), "LOCAL_DATABASE_URL=" + f.dsn, "REDIS_URL=unix://" + f.r.Options().Addr, "ORDINARY_GO_WORKER_MODE=" + request.Operation + "-first-ownership", "ORDINARY_FIRST_OWNERSHIP_REQUEST_FILE=" + path, "ORDINARY_OWNERSHIP_SOURCE_REVISION=" + request.SourceRevision, "ORDINARY_OWNERSHIP_PLAN_SHA256=" + request.PlanSHA256, "ORDINARY_OWNERSHIP_PROJECTION_SHA1=" + request.ProjectionSHA1, "ORDINARY_OWNERSHIP_ROUTING_EPOCH=" + strconv.FormatInt(request.RoutingEpoch, 10), "CRAWLER_IMAGE_REF=" + request.CrawlerImageRef, "LIGHTPANDA_B0_ROUTING_EPOCH=" + strconv.FormatInt(request.RoutingEpoch, 10), "LIGHTPANDA_B0_QUEUE_NAMESPACE=" + request.Namespace, "LIGHTPANDA_B0_SHARD_ID=" + request.ShardID, "LIGHTPANDA_B0_PRODUCER_COHORT=" + request.B0Cohort}
		out, err := command.CombinedOutput()
		if !accepted {
			if err == nil {
				t.Fatal("unacknowledged SAVE accepted")
			}
			return
		}
		var result queue.FirstOwnershipResult
		if err != nil || json.Unmarshal(out, &result) != nil || result.PlanSHA256 != plan.SHA256() {
			t.Fatal("native executable rejected canonical repair lifecycle", err)
		}
	}
	f.r.Do(ctx, "ACL", "SETUSER", "default", "-save")
	call(false)
	var state string
	if err := f.pg.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", plan.SHA256()).Scan(&state); err != nil || state != "staged" {
		t.Fatal("failed SAVE granted SQL ownership", state, err)
	}
	f.r.Do(ctx, "ACL", "SETUSER", "default", "+save")
	call(true)
	if f.r.HGet(ctx, "scrape:"+f.original, "board_id").Val() != board || f.r.ZScore(ctx, "ft_scrapes_simple:example.com", f.original).Err() != nil || f.r.ZScore(ctx, "ft_scrapes_browser:example.com", f.original).Err() != redis.Nil {
		t.Fatal("installed executable did not repair canonical identity and lane")
	}
	authority, err := queue.OpenOwnedAuthority(ctx, f.dsn, f.client, plan.Epoch(), plan.SHA256(), plan.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	defer authority.Close()
	claim, err := authority.Claim(ctx, queue.Simple)
	if err != nil || claim == nil || claim.Descriptor().ID != f.original {
		t.Fatal("installed repaired detail unclaimable", err)
	}
	request.Operation = "retire"
	call(true)
	var afterDue time.Time
	if err := f.pg.QueryRow(ctx, "SELECT next_scrape_at FROM job_posting WHERE id=$1::uuid", f.original).Scan(&afterDue); err != nil || !afterDue.Equal(due) || f.r.ZScore(ctx, "scrapes_simple:example.com", f.original).Val() != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("installed retirement lost repaired canonical deadline", err)
	}
}
