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
