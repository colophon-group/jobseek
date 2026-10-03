package worker

import (
	"strings"
	"testing"
)

func TestColdOrdinaryFinalizationAdminBindsExplicitRollbackAndClosedInputs(t *testing.T) {
	for _, op := range []string{"cold-ordinary-finalization-plan", "cold-ordinary-finalization-retain", "cold-ordinary-finalization-prepare", "cold-ordinary-finalization-publish", "cold-ordinary-finalization-complete", "cold-ordinary-finalization-inspect"} {
		base := map[string]string{
			"ORDINARY_GO_WORKER_MODE": op, "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("a", 40),
			"LOCAL_DATABASE_URL": "postgresql://private:secret@localhost/fixture", "REDIS_URL": "unix:///private/redis.sock",
			"ORDINARY_COLD_ROUTING_EPOCH": "146", "ORDINARY_COLD_RETIREMENT_EPOCH": "147",
			"ORDINARY_COLD_INTENT_FILE": "/private/intent.json", "ORDINARY_COLD_INTENT_SHA256": strings.Repeat("b", 64),
			"ORDINARY_COLD_REVERSAL_FILE": "/private/reversal.json", "ORDINARY_COLD_REVERSAL_SHA256": strings.Repeat("c", 64),
			"ORDINARY_COLD_PLAN_SHA256": strings.Repeat("d", 64), "ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256": strings.Repeat("e", 64),
			"ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256": strings.Repeat("f", 64),
			"ORDINARY_COLD_FINALIZATION_REQUEST_FILE":   "/private/finalization.json", "ORDINARY_COLD_FINALIZATION_REQUEST_SHA256": strings.Repeat("1", 64),
		}
		if op != "cold-ordinary-finalization-plan" {
			base["ORDINARY_COLD_FINALIZATION_PLAN_SHA256"] = strings.Repeat("2", 64)
		}
		if op != "cold-ordinary-finalization-inspect" {
			base["ORDINARY_COLD_B0_TARGET_FILE"], base["ORDINARY_COLD_B0_TARGET_SHA256"], base["ORDINARY_COLD_B0_LUA_FILE"] = "/private/target.json", strings.Repeat("3", 64), "/private/b0.lua"
		} else {
			delete(base, "REDIS_URL")
		}
		getenv := func(key string) string { return base[key] }
		if ColdAdminOperation("--"+op) != op {
			t.Fatal("finalization command missing")
		}
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != nil {
			t.Fatal("explicit finalization config refused", err)
		}
		for key, value := range map[string]string{
			"ORDINARY_GO_WORKER_MODE": "enabled", "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("b", 40),
			"ORDINARY_COLD_ROUTING_EPOCH": "0146", "ORDINARY_COLD_RETIREMENT_EPOCH": "146",
			"ORDINARY_COLD_INTENT_FILE": "relative", "ORDINARY_COLD_REVERSAL_SHA256": "latest",
			"ORDINARY_COLD_PLAN_SHA256": "", "ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256": "", "ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256": "",
			"ORDINARY_COLD_FINALIZATION_REQUEST_FILE": "relative", "ORDINARY_COLD_FINALIZATION_REQUEST_SHA256": "latest",
			"ORDINARY_OWNERSHIP_ROUTING_EPOCH": "147", "ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256": strings.Repeat("4", 64),
			"ORDINARY_COLD_B0_RESTORATION_PLAN_SHA256": strings.Repeat("5", 64), "ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_FILE": "/private/unused.json",
			"ORDINARY_COLD_PRIOR_B0_RECEIPT_FILE": "/private/unused", "ORDINARY_COLD_PRIOR_B0_RECEIPT_SHA256": strings.Repeat("6", 64),
		} {
			old := base[key]
			base[key] = value
			if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != ErrStartup || strings.Contains(err.Error(), "secret") {
				t.Fatal("ambiguous finalization config admitted or exposed", key)
			}
			base[key] = old
		}
		old := base["ORDINARY_COLD_FINALIZATION_PLAN_SHA256"]
		if op == "cold-ordinary-finalization-plan" {
			base["ORDINARY_COLD_FINALIZATION_PLAN_SHA256"] = strings.Repeat("2", 64)
		} else {
			base["ORDINARY_COLD_FINALIZATION_PLAN_SHA256"] = ""
		}
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != ErrStartup {
			t.Fatal("missing/extraneous approval admitted")
		}
		base["ORDINARY_COLD_FINALIZATION_PLAN_SHA256"] = old
		if op == "cold-ordinary-finalization-inspect" {
			for _, key := range []string{"ORDINARY_COLD_B0_TARGET_FILE", "ORDINARY_COLD_B0_TARGET_SHA256", "ORDINARY_COLD_B0_LUA_FILE"} {
				base[key] = "/private/unused"
				if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != ErrStartup {
					t.Fatal("inspection admitted live target input")
				}
				delete(base, key)
			}
		}
		base["ORDINARY_GO_WORKER_MODE"] = "cold-b0-reactivation-inspect"
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), "cold-b0-reactivation-inspect"); err != ErrStartup {
			t.Fatal("unrelated command admitted finalizer inputs")
		}
	}
}
