package executor

import (
	"strings"
	"testing"
)

func TestExecutorEnvironmentBoundsAndCredentialSeparation(t *testing.T) {
	base := map[string]string{"LIGHTPANDA_B0_EXECUTOR_MODE": "enabled", "CRAWLER_DB_POOL_MIN": "1", "CRAWLER_DB_POOL_MAX": "1", "LIGHTPANDA_B0_SHARD_ID": "lightpanda-b0", "LIGHTPANDA_B0_ROUTING_EPOCH": "141", "LOCAL_DATABASE_URL": "fixture-only-not-a-credential"}
	get := func(key string) string { return base[key] }
	config, err := ReadRuntimeConfig(get)
	if err != nil || config.Epoch != 141 || config.Shard != "lightpanda-b0" {
		t.Fatal("valid environment rejected")
	}
	for key, values := range map[string][]string{
		"LIGHTPANDA_B0_EXECUTOR_MODE": {"", "true", "Enabled"}, "CRAWLER_DB_POOL_MIN": {"", "0", "01", "2"}, "CRAWLER_DB_POOL_MAX": {"", "4"},
		"LIGHTPANDA_B0_SHARD_ID": {"", "other"}, "LIGHTPANDA_B0_EXECUTOR_SOCKET": {"/tmp/executor.sock", SocketPath + "/"},
		"LIGHTPANDA_B0_ROUTING_EPOCH": {"", "0", "01", "-1", "+1", "1.0", "1e1", " 1", "１", "10000000000000", "9223372036854775808"}, "LOCAL_DATABASE_URL": {""},
	} {
		for _, value := range values {
			old := base[key]
			base[key] = value
			if _, err := ReadRuntimeConfig(get); err == nil {
				t.Fatalf("accepted invalid %s", key)
			}
			base[key] = old
		}
	}
	for _, key := range forbiddenEnvironment {
		base[key] = "private-fixture-sentinel"
		_, err := ReadRuntimeConfig(get)
		if err == nil || strings.Contains(err.Error(), base[key]) {
			t.Fatal("authority credential accepted or exposed")
		}
		delete(base, key)
	}
	base["LIGHTPANDA_B0_ROUTING_EPOCH"] = "9999999999999"
	base["LIGHTPANDA_B0_EXECUTOR_SOCKET"] = SocketPath
	if _, err := ReadRuntimeConfig(get); err != nil {
		t.Fatal("inclusive epoch maximum rejected")
	}
}
