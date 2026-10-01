package worker

import (
	"strings"
	"testing"
)

func TestColdB0ReactivationAdminRequiresExactProtectedRollbackAuthority(t *testing.T) {
	for _, op := range []string{"cold-b0-reactivation-plan", "cold-b0-reactivation-retain", "cold-b0-reactivation-apply", "cold-b0-reactivation-inspect"} {
		base := map[string]string{
			"ORDINARY_GO_WORKER_MODE": op, "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("a", 40),
			"LOCAL_DATABASE_URL": "postgresql://private:secret@localhost/fixture", "REDIS_URL": "unix:///private/redis.sock",
			"ORDINARY_COLD_ROUTING_EPOCH": "146", "ORDINARY_COLD_RETIREMENT_EPOCH": "147",
			"ORDINARY_COLD_INTENT_FILE": "/private/intent.json", "ORDINARY_COLD_INTENT_SHA256": strings.Repeat("b", 64),
			"ORDINARY_COLD_REVERSAL_FILE": "/private/reversal.json", "ORDINARY_COLD_REVERSAL_SHA256": strings.Repeat("c", 64),
			"ORDINARY_COLD_PLAN_SHA256": strings.Repeat("d", 64), "ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256": strings.Repeat("e", 64),
		}
		if op != "cold-b0-reactivation-plan" {
			base["ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256"] = strings.Repeat("f", 64)
		}
		if op != "cold-b0-reactivation-inspect" {
			base["ORDINARY_COLD_B0_TARGET_FILE"], base["ORDINARY_COLD_B0_TARGET_SHA256"], base["ORDINARY_COLD_B0_LUA_FILE"] = "/private/target.json", strings.Repeat("1", 64), "/private/b0.lua"
			base["ORDINARY_COLD_PRIOR_B0_RECEIPT_FILE"], base["ORDINARY_COLD_PRIOR_B0_RECEIPT_SHA256"] = "/private/receipt", strings.Repeat("2", 64)
		}
		getenv := func(key string) string { return base[key] }
		if ColdAdminOperation("--"+op) != op {
			t.Fatal("reactivation command missing")
		}
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != nil {
			t.Fatal("explicit protected reactivation config refused", err)
		}
		for key, value := range map[string]string{
			"ORDINARY_GO_WORKER_MODE": "enabled", "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("b", 40),
			"ORDINARY_COLD_ROUTING_EPOCH": "0146", "ORDINARY_COLD_RETIREMENT_EPOCH": "146",
			"ORDINARY_COLD_INTENT_FILE": "relative", "ORDINARY_COLD_REVERSAL_SHA256": "latest",
			"ORDINARY_COLD_PLAN_SHA256": "", "ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256": "",
			"ORDINARY_OWNERSHIP_ROUTING_EPOCH": "147", "ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256": strings.Repeat("3", 64),
			"ORDINARY_COLD_B0_RESTORATION_PLAN_SHA256": strings.Repeat("4", 64), "ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_FILE": "/private/unused.json",
		} {
			old := base[key]
			base[key] = value
			if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != ErrStartup || strings.Contains(err.Error(), "secret") {
				t.Fatal("ambiguous reactivation authority admitted or exposed", key)
			}
			base[key] = old
		}
		selector := base["ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256"]
		base["ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256"] = ""
		if op == "cold-b0-reactivation-plan" {
			base["ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256"] = strings.Repeat("f", 64)
		}
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != ErrStartup {
			t.Fatal("missing/extraneous approval admitted")
		}
		base["ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256"] = selector
		if op == "cold-b0-reactivation-inspect" {
			for _, key := range []string{"ORDINARY_COLD_B0_LUA_FILE", "ORDINARY_COLD_B0_TARGET_FILE", "ORDINARY_COLD_PRIOR_B0_RECEIPT_FILE"} {
				base[key] = "/private/unused"
				if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), op); err != ErrStartup {
					t.Fatal("historical inspection admitted unused live input")
				}
				delete(base, key)
			}
		}
		base["ORDINARY_GO_WORKER_MODE"] = "cold-reversal-inspect"
		if _, err := ReadColdAdminConfig(getenv, strings.Repeat("a", 40), "cold-reversal-inspect"); err != ErrStartup {
			t.Fatal("unrelated command admitted reactivation inputs")
		}
	}
}
