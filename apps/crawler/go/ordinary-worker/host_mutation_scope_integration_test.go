package worker

import (
	"context"
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealHostMutationScopeKeepsFlockAfterColdSQLRelease(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	path := filepath.Join(hostPrivateDirectory(t), "mutation.lock")
	var escaped context.Context
	var sqlScope *queue.HostColdSQL
	var observation struct {
		PID  int32   `json:"backend_pid"`
		Keys []int64 `json:"exclusive_barriers"`
	}
	binding := queue.HostColdSQLBinding{SourceRevision: ordinaryFixtureSourceRevision(t), RequestSHA256: strings.Repeat("a", 64), ContainmentIntentSHA256: strings.Repeat("b", 64)}
	if err := withHostMutationScope(ctx, path, func(outer context.Context) error {
		lock, release, err := acquireHostPhaseLock(outer, path)
		if err != nil {
			return err
		}
		fd := lock.file.Fd()
		err = queue.WithHostColdSQL(outer, f.pg, binding, func(cold context.Context, sql *queue.HostColdSQL) error {
			escaped, sqlScope = cold, sql
			if json.Unmarshal([]byte(sql.Body()), &observation) != nil || observation.PID <= 0 || len(observation.Keys) != 3 || CheckHostMutationScope(cold) != nil {
				t.Fatal("joined outer lock and live SQL session")
			}
			for _, key := range observation.Keys {
				var entered bool
				if f.pg.QueryRow(cold, "SELECT pg_try_advisory_xact_lock_shared($1)", key).Scan(&entered) != nil || entered {
					t.Fatal("writer entered cold session")
				}
			}
			assertHostScopeFlock(t, path, true)
			return nil
		})
		release()
		if err != nil {
			return err
		}
		if sqlScope.Check(escaped) == nil || queue.CheckHostColdSQLScope(outer, f.pg, binding.SourceRevision) == nil || CheckHostMutationScope(outer) != nil {
			t.Fatal("SQL escape or premature outer-lock release")
		}
		var sessions int
		if f.pg.QueryRow(outer, "SELECT count(*) FROM pg_stat_activity WHERE pid=$1", observation.PID).Scan(&sessions) != nil || sessions != 0 {
			t.Fatal("cold SQL backend survived scope release")
		}
		for _, key := range observation.Keys {
			var entered bool
			if f.pg.QueryRow(outer, "SELECT pg_try_advisory_xact_lock_shared($1)", key).Scan(&entered) != nil || !entered {
				t.Fatal("SQL barrier remained after release")
			}
		}
		assertHostScopeFlock(t, path, true)
		// Later phases retain the same descriptor after SQL has ended. This is
		// an exclusion test; it does not start or attest production services.
		post, finish, err := acquireHostPhaseLock(outer, path)
		if err != nil || post.file.Fd() != fd || post.verify() != nil {
			t.Fatal("post-SQL phase replaced original flock", err)
		}
		finish()
		assertHostScopeFlock(t, path, true)
		return nil
	}); err != nil {
		t.Fatal("private joined lifecycle", err)
	}
	assertHostScopeFlock(t, path, false)
	t.Log("actual private host mutation scope keeps original flock across live exclusive SQL barriers, destroys cold backend and releases all three SQL barriers before a later phase, rejects escaped SQL authority and releases host flock only after outer callback; actual production restoration/startup/readiness and runtime admission unproven")
}
