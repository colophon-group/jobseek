package queue

import (
	"context"
	"errors"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestReviewedLuaCopiesMatchAuthority(t *testing.T) {
	for name, source := range map[string]string{"claim_work.lua": claimLua, "heartbeat_task.lua": heartbeatLua, "complete_task.lua": completeLua, "reschedule_task.lua": rescheduleLua} {
		body, err := os.ReadFile(filepath.Join("../../src/lua", name))
		if err != nil || string(body) != source {
			t.Fatalf("reviewed %s differs", name)
		}
	}
}
func TestRejectConfigurationBeforeConnecting(t *testing.T) {
	settings := Settings{LeaseTTL: time.Minute, MaxDomains: 10}
	for _, bad := range []Settings{{LeaseTTL: 0, MaxDomains: 10}, {LeaseTTL: time.Minute, MaxDomains: 0}, {LeaseTTL: time.Minute, MaxDomains: 10, DefaultDelaySeconds: math.NaN()}, {LeaseTTL: time.Minute, MaxDomains: 10, DefaultDelaySeconds: math.Inf(1)}, {LeaseTTL: time.Minute, MaxDomains: 10, DefaultDelaySeconds: -1}} {
		if _, err := Open("redis://127.0.0.1:1", bad); !errors.Is(err, ErrConfiguration) {
			t.Fatal("invalid bounds accepted")
		}
	}
	if _, err := Open("unsupported://fixture", settings); !errors.Is(err, ErrConfiguration) {
		t.Fatal("invalid connection accepted")
	}
	c, err := Open("redis://127.0.0.1:1", settings)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if c.redis.Options().MaxRetries != 0 || c.redis.Options().Protocol != 2 || c.redis.Options().PoolSize != 2 {
		t.Fatal("mutation transport contract differs")
	}
	if _, err := c.Claim(context.Background(), WorkerType("invalid")); !errors.Is(err, ErrConfiguration) {
		t.Fatal("invalid queue selected")
	}
	for _, task := range []*Task{nil, {Worker: Simple, Kind: Monitor, ID: "a|b", Domain: "fixture.invalid"}, {Worker: Simple, Kind: Kind("invalid"), ID: "a", Domain: "fixture.invalid"}, {Worker: Simple, Kind: Scrape, ID: "a", Domain: "bad\norigin"}} {
		if _, err := c.Complete(context.Background(), task); !errors.Is(err, ErrConfiguration) {
			t.Fatal("invalid descriptor accepted")
		}
	}
}

// Own a private, persistence-free Unix Redis process. Never connect to a shared
// database or clear arbitrary caller state. Linux CI must install Redis before
// this integration evidence can be claimed; a skip establishes no proof.
func privateRedis(t *testing.T) *Client {
	t.Helper()
	binary, err := exec.LookPath("redis-server")
	if err != nil {
		t.Skip("private real Redis fixture unavailable")
	}
	// Unix socket paths are bounded on macOS/Linux; test-name temp paths can
	// exceed that bound before Redis starts. Own a short private directory.
	root, err := os.MkdirTemp("/tmp", "jq-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(root) })
	log, err := os.OpenFile(filepath.Join(root, "redis.log"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(root, "redis.sock")
	command := exec.Command(binary, "--port", "0", "--unixsocket", socket, "--unixsocketperm", "700", "--save", "", "--appendonly", "no", "--dir", root)
	command.Env = []string{"PATH=" + os.Getenv("PATH"), "LC_ALL=C", "LANG=C"}
	command.Stdout, command.Stderr = log, log
	if err := command.Start(); err != nil {
		_ = log.Close()
		t.Fatal("owned Redis fixture did not start")
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	t.Cleanup(func() {
		_ = command.Process.Signal(os.Interrupt)
		select {
		case <-done:
		case <-time.After(3 * time.Second):
			_ = command.Process.Kill()
			<-done
		}
		_ = log.Close()
	})
	c, err := Open("unix://"+socket, Settings{LeaseTTL: time.Minute, MaxDomains: 10})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = c.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	for {
		if c.redis.Ping(ctx).Err() == nil {
			break
		}
		if ctx.Err() != nil {
			t.Fatal("owned Redis fixture readiness expired")
		}
		time.Sleep(10 * time.Millisecond)
	}
	return c
}
func seedTask(t *testing.T, c *Client, kind Kind, worker WorkerType) (*Task, float64) {
	t.Helper()
	ctx := context.Background()
	now, err := c.clock(ctx)
	if err != nil {
		t.Fatal(err)
	}
	task := &Task{Worker: worker, Kind: kind, ID: "00000000-0000-4000-8000-000000000001", Domain: "ordinary-worker.invalid"}
	prefix, tier := "monitors_", ":1"
	config := "board:"
	if kind == Scrape {
		prefix, tier, config = "scrapes_", ":2", "scrape:"
	}
	if err := c.redis.HSet(ctx, config+task.ID, map[string]string{"domain": task.Domain, "crawler_type": "greenhouse", "board_id": task.ID, "source_url": "https://ordinary-worker.invalid/held"}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := c.redis.ZAdd(ctx, prefix+string(worker)+":"+task.Domain, redis.Z{Score: now - 1, Member: task.ID}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := c.redis.ZAdd(ctx, "ready:"+string(worker)+tier, redis.Z{Score: now - 1, Member: task.Domain}).Err(); err != nil {
		t.Fatal(err)
	}
	return task, now
}
func inflight(task *Task) string { return string(task.Kind) + "|" + task.Domain + "|" + task.ID }
func TestRealRedisClaimHeartbeatAndDescriptionSchedule(t *testing.T) {
	for _, worker := range []WorkerType{Simple, Browser} {
		t.Run(string(worker), func(t *testing.T) {
			c := privateRedis(t)
			task, now := seedTask(t, c, Scrape, worker)
			ctx := context.Background()
			claimed, err := c.Claim(ctx, worker)
			if err != nil || claimed == nil || claimed.ID != task.ID || claimed.Domain != task.Domain || claimed.Kind != Scrape || claimed.Config["source_url"] == "" {
				t.Fatal("real claim descriptor/config differs")
			}
			score, err := c.redis.ZScore(ctx, "inflight:"+string(worker), inflight(task)).Result()
			if err != nil || score != claimed.InitialLeaseUntil {
				t.Fatal("claimed Redis deadline differs")
			}
			time.Sleep(2 * time.Millisecond)
			if updated, err := c.Heartbeat(ctx, claimed); err != nil || !updated {
				t.Fatal("real lease did not advance")
			}
			next := now + 3600
			if accepted, err := c.Reschedule(ctx, claimed, next); err != nil || !accepted {
				t.Fatal("real reschedule rejected")
			}
			score, err = c.redis.ZScore(ctx, "scrapes_"+string(worker)+":"+task.Domain, task.ID).Result()
			if err != nil || score != next {
				t.Fatal("database-supplied due timestamp changed")
			}
			if count, err := c.redis.ZCard(ctx, "inflight:"+string(worker)).Result(); err != nil || count != 0 {
				t.Fatal("reschedule retained lease")
			}
			if exists, err := c.redis.Exists(ctx, "scrape:"+task.ID).Result(); err != nil || exists != 1 {
				t.Fatal("reschedule deleted live config")
			}
			if removed, err := c.Complete(ctx, claimed); err != nil || removed {
				t.Fatal("post-reschedule cleanup differs")
			}
		})
	}
}
func TestRealRedisRepairDeadlineAndOrphanCleanup(t *testing.T) {
	t.Run("repair-completion", func(t *testing.T) {
		c := privateRedis(t)
		task, now := seedTask(t, c, Monitor, Simple)
		ctx := context.Background()
		claimed, err := c.Claim(ctx, Simple)
		if err != nil || claimed == nil {
			t.Fatal("monitor claim failed")
		}
		if err := c.redis.HSet(ctx, "monitor_repair_due:simple", inflight(task), number(now+10)).Err(); err != nil {
			t.Fatal(err)
		}
		if removed, err := c.Complete(ctx, claimed); err != nil || removed {
			t.Fatal("pending repair was dropped")
		}
		if score, err := c.redis.ZScore(ctx, "inflight:simple", inflight(task)).Result(); err != nil || score != 0 {
			t.Fatal("pending repair did not expire for existing reaper")
		}
		if accepted, err := c.Reschedule(ctx, claimed, now+3600); err != nil || !accepted {
			t.Fatal("monitor repair reschedule failed")
		}
		if score, err := c.redis.ZScore(ctx, "monitors_simple:"+task.Domain, task.ID).Result(); err != nil || score != now+10 {
			t.Fatal("native reschedule postponed authoritative repair")
		}
	})
	t.Run("drained-scrape", func(t *testing.T) {
		c := privateRedis(t)
		task, _ := seedTask(t, c, Scrape, Simple)
		ctx := context.Background()
		claimed, err := c.Claim(ctx, Simple)
		if err != nil || claimed == nil {
			t.Fatal("scrape claim failed")
		}
		if removed, err := c.Complete(ctx, claimed); err != nil || !removed {
			t.Fatal("scrape completion failed")
		}
		if exists, err := c.redis.Exists(ctx, "scrape:"+task.ID).Result(); err != nil || exists != 0 {
			t.Fatal("orphan configuration survived completion")
		}
		if updated, err := c.Heartbeat(ctx, claimed); err != nil || updated {
			t.Fatal("heartbeat resurrected drained inflight task")
		}
	})
	t.Run("b0-guard", func(t *testing.T) {
		c := privateRedis(t)
		task, _ := seedTask(t, c, Scrape, Browser)
		ctx := context.Background()
		if err := c.redis.HSet(ctx, "lightpanda-b0:legacy-guard", task.ID, "owned-by-b0").Err(); err != nil {
			t.Fatal(err)
		}
		if claimed, err := c.Claim(ctx, Browser); err != nil || claimed != nil {
			t.Fatal("ordinary claimant stole guarded B0 work")
		}
		if count, err := c.redis.ZCard(ctx, "inflight:browser").Result(); err != nil || count != 0 {
			t.Fatal("guarded task received ordinary lease")
		}
	})
}
