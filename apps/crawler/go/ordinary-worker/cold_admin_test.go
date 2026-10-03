package worker

import (
	"strings"
	"testing"
)

func TestColdB0ForwardAdminRequiresProtectedSourceAndExactApproval(t *testing.T) {
	for _, operation := range []string{"cold-b0-forward-plan", "cold-b0-forward-retain", "cold-b0-forward-apply", "cold-b0-forward-inspect", "cold-forward-prepare", "cold-forward-publish", "cold-forward-activate"} {
		base := map[string]string{"ORDINARY_GO_WORKER_MODE": operation, "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("a", 40), "ORDINARY_COLD_ROUTING_EPOCH": "146", "LOCAL_DATABASE_URL": "postgresql://private:secret@localhost/fixture", "REDIS_URL": "unix:///private/redis.sock", "ORDINARY_COLD_INTENT_FILE": "/private/intent.json", "ORDINARY_COLD_INTENT_SHA256": strings.Repeat("b", 64), "ORDINARY_COLD_PLAN_SHA256": strings.Repeat("c", 64), "ORDINARY_COLD_B0_FORWARD_REQUEST_FILE": "/private/request.json", "ORDINARY_COLD_B0_FORWARD_REQUEST_SHA256": strings.Repeat("d", 64)}
		if operation != "cold-b0-forward-plan" {
			base["ORDINARY_COLD_B0_FORWARD_PLAN_SHA256"] = strings.Repeat("e", 64)
		}
		if operation != "cold-b0-forward-inspect" {
			base["ORDINARY_COLD_B0_TARGET_FILE"], base["ORDINARY_COLD_B0_TARGET_SHA256"], base["ORDINARY_COLD_B0_LUA_FILE"] = "/private/target.json", strings.Repeat("f", 64), "/private/queue.lua"
		}
		if coldForwardPublicationOperation(operation) {
			base["ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256"] = strings.Repeat("a", 64)
		}
		getenv := func(key string) string { return base[key] }
		if ColdAdminOperation("--"+operation) != operation {
			t.Fatal("explicit native forward command missing")
		}
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), operation); err != nil {
			t.Fatal("exact protected native forward config rejected", err)
		}
		for key, value := range map[string]string{"ORDINARY_GO_WORKER_MODE": "enabled", "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("f", 40), "ORDINARY_COLD_ROUTING_EPOCH": "0146", "ORDINARY_COLD_PLAN_SHA256": "", "ORDINARY_COLD_INTENT_FILE": "relative", "ORDINARY_COLD_B0_FORWARD_REQUEST_FILE": "relative", "ORDINARY_COLD_B0_FORWARD_REQUEST_SHA256": "latest", "ORDINARY_COLD_REVERSAL_FILE": "/private/unused.json", "ORDINARY_COLD_B0_RESTORATION_PLAN_SHA256": strings.Repeat("f", 64), "ORDINARY_OWNERSHIP_ROUTING_EPOCH": "146", "ORDINARY_COLD_B0_NAMESPACE": "adopt-latest"} {
			old := base[key]
			base[key] = value
			if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), operation); err != ErrStartup || strings.Contains(err.Error(), "secret") {
				t.Fatal("ambiguous forward authority admitted or exposed", key)
			}
			base[key] = old
		}
		if coldForwardPublicationOperation(operation) {
			for _, receipt := range []string{"", "latest", strings.Repeat("A", 64)} {
				base["ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256"] = receipt
				if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), operation); err != ErrStartup {
					t.Fatal("publication admitted missing/ambiguous completion")
				}
			}
			base["ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256"] = strings.Repeat("a", 64)
		} else {
			base["ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256"] = strings.Repeat("a", 64)
			if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), operation); err != ErrStartup {
				t.Fatal("unrelated forward operation admitted completion selector")
			}
			delete(base, "ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256")
		}
		old := base["ORDINARY_COLD_B0_FORWARD_PLAN_SHA256"]
		if operation == "cold-b0-forward-plan" {
			base["ORDINARY_COLD_B0_FORWARD_PLAN_SHA256"] = strings.Repeat("e", 64)
		} else {
			base["ORDINARY_COLD_B0_FORWARD_PLAN_SHA256"] = ""
		}
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), operation); err != ErrStartup {
			t.Fatal("preview/recovery approval was ambiguous")
		}
		base["ORDINARY_COLD_B0_FORWARD_PLAN_SHA256"] = old
		if operation == "cold-b0-forward-inspect" {
			base["ORDINARY_COLD_B0_LUA_FILE"] = "/private/unused.lua"
			if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), operation); err != ErrStartup {
				t.Fatal("read-only inspection admitted unused script")
			}
			delete(base, "ORDINARY_COLD_B0_LUA_FILE")
		}
		base["ORDINARY_GO_WORKER_MODE"] = "cold-inspect"
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), "cold-inspect"); err != ErrStartup {
			t.Fatal("unrelated operation admitted forward fields")
		}
	}
}

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

func TestColdOrdinaryRestorationAdminRequiresProtectedExactDecision(t *testing.T) {
	for _, op := range []string{"cold-ordinary-rollback-plan", "cold-ordinary-rollback-retain", "cold-ordinary-rollback-inspect"} {
		base := map[string]string{"ORDINARY_GO_WORKER_MODE": op, "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("a", 40), "ORDINARY_COLD_ROUTING_EPOCH": "146", "LOCAL_DATABASE_URL": "postgresql://private:secret@localhost/fixture", "REDIS_URL": "unix:///private/redis.sock", "ORDINARY_COLD_INTENT_FILE": "/private/intent.json", "ORDINARY_COLD_INTENT_SHA256": strings.Repeat("b", 64), "ORDINARY_COLD_PLAN_SHA256": strings.Repeat("c", 64), "ORDINARY_COLD_REVERSAL_FILE": "/private/reversal.json", "ORDINARY_COLD_REVERSAL_SHA256": strings.Repeat("d", 64), "ORDINARY_COLD_B0_RESTORATION_PLAN_SHA256": strings.Repeat("e", 64), "ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_FILE": "/private/request.json", "ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_SHA256": strings.Repeat("f", 64)}
		if op != "cold-ordinary-rollback-plan" {
			base["ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256"] = strings.Repeat("1", 64)
		}
		if op != "cold-ordinary-rollback-inspect" {
			base["ORDINARY_COLD_B0_TARGET_FILE"], base["ORDINARY_COLD_B0_TARGET_SHA256"], base["ORDINARY_COLD_B0_LUA_FILE"] = "/private/target.json", strings.Repeat("2", 64), "/private/b0.lua"
		}
		getenv := func(key string) string { return base[key] }
		if ColdAdminOperation("--"+op) != op {
			t.Fatal("explicit ordinary rollback command missing")
		}
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != nil {
			t.Fatal("protected decision config refused", err)
		}
		for key, value := range map[string]string{"ORDINARY_GO_WORKER_MODE": "enabled", "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("b", 40), "ORDINARY_COLD_ROUTING_EPOCH": "0146", "ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_FILE": "relative", "ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_SHA256": "latest", "ORDINARY_COLD_B0_RESTORATION_PLAN_SHA256": "", "ORDINARY_COLD_REVERSAL_FILE": "", "ORDINARY_COLD_PLAN_SHA256": "", "ORDINARY_COLD_B0_RESTORE_REQUEST_FILE": "/private/unused.json", "ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256": strings.Repeat("3", 64), "ORDINARY_OWNERSHIP_ROUTING_EPOCH": "146"} {
			old := base[key]
			base[key] = value
			if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != ErrStartup || strings.Contains(err.Error(), "secret") {
				t.Fatal("ambiguous ordinary rollback authority admitted or exposed", key)
			}
			base[key] = old
		}
		old := base["ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256"]
		if op == "cold-ordinary-rollback-plan" {
			base["ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256"] = strings.Repeat("1", 64)
		} else {
			base["ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256"] = ""
		}
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != ErrStartup {
			t.Fatal("preview/retention selector ambiguous")
		}
		base["ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256"] = old
		if op == "cold-ordinary-rollback-inspect" {
			base["ORDINARY_COLD_B0_LUA_FILE"] = "/private/unused.lua"
			if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != ErrStartup {
				t.Fatal("inspection admitted unused Lua")
			}
			delete(base, "ORDINARY_COLD_B0_LUA_FILE")
		}
		base["ORDINARY_GO_WORKER_MODE"] = "cold-reversal-inspect"
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), "cold-reversal-inspect"); err != ErrStartup {
			t.Fatal("unrelated operation admitted decision fields")
		}
	}
}
