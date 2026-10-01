package worker

import (
	"strings"
	"testing"
)

func TestColdAdminRequiresProtectedOperationSourceEpochAndExactFiles(t *testing.T) {
	base := map[string]string{"ORDINARY_GO_WORKER_MODE": "cold-publish", "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("a", 40), "ORDINARY_COLD_ROUTING_EPOCH": "146", "LOCAL_DATABASE_URL": "postgresql://private:secret@localhost/fixture", "REDIS_URL": "unix:///private/redis.sock", "ORDINARY_COLD_INTENT_FILE": "/private/intent.json", "ORDINARY_COLD_INTENT_SHA256": strings.Repeat("b", 64), "ORDINARY_COLD_B0_TARGET_FILE": "/private/target.json", "ORDINARY_COLD_B0_TARGET_SHA256": strings.Repeat("c", 64), "ORDINARY_COLD_B0_LUA_FILE": "/private/queue.lua", "ORDINARY_COLD_PLAN_SHA256": strings.Repeat("d", 64)}
	getenv := func(k string) string { return base[k] }
	if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), "cold-publish"); err != nil {
		t.Fatal(err)
	}
	for key, value := range map[string]string{"ORDINARY_GO_WORKER_MODE": "enabled", "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("b", 40), "ORDINARY_COLD_ROUTING_EPOCH": "0146", "ORDINARY_COLD_INTENT_FILE": "relative", "ORDINARY_COLD_INTENT_SHA256": "latest", "ORDINARY_COLD_B0_TARGET_FILE": "", "ORDINARY_COLD_B0_TARGET_SHA256": "", "ORDINARY_COLD_B0_LUA_FILE": "", "ORDINARY_COLD_PLAN_SHA256": "", "ORDINARY_OWNERSHIP_ROUTING_EPOCH": "146", "ORDINARY_OWNERSHIP_PLAN_SHA256": strings.Repeat("d", 64), "ORDINARY_OWNERSHIP_PROJECTION_SHA1": strings.Repeat("e", 40), "ORDINARY_GO_COHORT_FILE": "/private/cohort.json", "ORDINARY_COLD_B0_NAMESPACE": "ordinary-private", "LOCAL_DATABASE_URL": "", "REDIS_URL": ""} {
		old := base[key]
		base[key] = value
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), "cold-publish"); err != ErrStartup || strings.Contains(err.Error(), "secret") {
			t.Fatal("ambiguous cold environment accepted or exposed input", key)
		}
		base[key] = old
	}
	for _, op := range []string{"", "cold-latest", "cold-reverse", "enabled", "cold-reserve", "cold-begin", "cold-b0-target"} {
		base["ORDINARY_GO_WORKER_MODE"] = op
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != ErrStartup {
			t.Fatal("unknown operation or incompatible fields accepted")
		}
	}
}

func TestColdReversalAdminRequiresExactApprovedSourceAndProtectedIntent(t *testing.T) {
	base := map[string]string{"ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("a", 40), "ORDINARY_COLD_ROUTING_EPOCH": "146", "LOCAL_DATABASE_URL": "postgresql://private:secret@localhost/fixture", "REDIS_URL": "unix:///private/redis.sock", "ORDINARY_COLD_INTENT_FILE": "/private/forward.json", "ORDINARY_COLD_INTENT_SHA256": strings.Repeat("b", 64), "ORDINARY_COLD_PLAN_SHA256": strings.Repeat("c", 64), "ORDINARY_COLD_REVERSAL_FILE": "/private/reversal.json", "ORDINARY_COLD_REVERSAL_SHA256": strings.Repeat("d", 64)}
	getenv := func(k string) string { return base[k] }
	for _, op := range []string{"cold-reversal-begin", "cold-reversal-reserve", "cold-reversal-inspect"} {
		base["ORDINARY_GO_WORKER_MODE"] = op
		if ColdAdminOperation("--"+op) != op {
			t.Fatal("explicit reversal operation unavailable")
		}
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != nil {
			t.Fatal("bounded protected reversal rejected")
		}
		for key, value := range map[string]string{"ORDINARY_GO_WORKER_MODE": "enabled", "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("b", 40), "ORDINARY_COLD_ROUTING_EPOCH": "0146", "ORDINARY_COLD_INTENT_FILE": "", "ORDINARY_COLD_INTENT_SHA256": "latest", "ORDINARY_COLD_PLAN_SHA256": "", "ORDINARY_COLD_REVERSAL_FILE": "relative", "ORDINARY_COLD_REVERSAL_SHA256": "", "ORDINARY_COLD_B0_TARGET_FILE": "/private/target.json", "ORDINARY_COLD_B0_LUA_FILE": "/private/queue.lua", "ORDINARY_OWNERSHIP_PLAN_SHA256": strings.Repeat("e", 64), "ORDINARY_COLD_B0_COHORT": "c1"} {
			old := base[key]
			base[key] = value
			if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != ErrStartup || strings.Contains(err.Error(), "secret") {
				t.Fatal("unapproved reversal config accepted or exposed input")
			}
			base[key] = old
		}
	}
}

func TestColdB0RestorationAdminRequiresExplicitProtectedApproval(t *testing.T) {
	for _, operation := range []string{"cold-b0-rollback-plan", "cold-b0-rollback-retain", "cold-b0-rollback-restore", "cold-b0-rollback-inspect"} {
		base := map[string]string{"ORDINARY_GO_WORKER_MODE": operation, "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("a", 40), "ORDINARY_COLD_ROUTING_EPOCH": "146", "LOCAL_DATABASE_URL": "postgresql://private:secret@localhost/fixture", "REDIS_URL": "unix:///private/redis.sock", "ORDINARY_COLD_INTENT_FILE": "/private/forward.json", "ORDINARY_COLD_INTENT_SHA256": strings.Repeat("b", 64), "ORDINARY_COLD_PLAN_SHA256": strings.Repeat("c", 64), "ORDINARY_COLD_REVERSAL_FILE": "/private/reversal.json", "ORDINARY_COLD_REVERSAL_SHA256": strings.Repeat("d", 64), "ORDINARY_COLD_B0_RESTORE_REQUEST_FILE": "/private/request.json", "ORDINARY_COLD_B0_RESTORE_REQUEST_SHA256": strings.Repeat("e", 64)}
		if operation != "cold-b0-rollback-plan" {
			base["ORDINARY_COLD_B0_RESTORATION_PLAN_SHA256"] = strings.Repeat("f", 64)
		}
		if operation != "cold-b0-rollback-inspect" {
			base["ORDINARY_COLD_B0_TARGET_FILE"] = "/private/target.json"
			base["ORDINARY_COLD_B0_TARGET_SHA256"] = strings.Repeat("1", 64)
			base["ORDINARY_COLD_B0_LUA_FILE"] = "/private/queue.lua"
		}
		getenv := func(key string) string { return base[key] }
		if ColdAdminOperation("--"+operation) != operation {
			t.Fatal("explicit protected command missing")
		}
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), operation); err != nil {
			t.Fatal("protected restoration config rejected", err)
		}
		for key, value := range map[string]string{"ORDINARY_GO_WORKER_MODE": "enabled", "ORDINARY_COLD_B0_RESTORE_REQUEST_FILE": "relative", "ORDINARY_COLD_B0_RESTORE_REQUEST_SHA256": "latest", "ORDINARY_COLD_REVERSAL_FILE": "", "ORDINARY_COLD_PLAN_SHA256": "", "ORDINARY_COLD_ROUTING_EPOCH": "latest", "ORDINARY_OWNERSHIP_ROUTING_EPOCH": "146", "ORDINARY_COLD_B0_NAMESPACE": "adopt-latest"} {
			old := base[key]
			base[key] = value
			if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), operation); err != ErrStartup || strings.Contains(err.Error(), "secret") {
				t.Fatal("ambiguous/unprotected approval admitted or exposed")
			}
			base[key] = old
		}
		if operation == "cold-b0-rollback-inspect" {
			base["ORDINARY_COLD_B0_LUA_FILE"] = "/private/unused.lua"
			if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), operation); err != ErrStartup {
				t.Fatal("inspection opened unused script authority")
			}
		}
		base["ORDINARY_GO_WORKER_MODE"] = "cold-reversal-inspect"
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), "cold-reversal-inspect"); err != ErrStartup {
			t.Fatal("unused restoration fields accepted by reversal")
		}
	}
}
