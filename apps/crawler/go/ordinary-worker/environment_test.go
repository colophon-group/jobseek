package worker

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"
	"time"
)

func fixtureRuntimeEnvironment() map[string]string {
	return map[string]string{
		"ORDINARY_GO_WORKER_MODE": "enabled", "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("a", 40), "ORDINARY_OWNERSHIP_PLAN_SHA256": strings.Repeat("b", 64), "ORDINARY_OWNERSHIP_PROJECTION_SHA1": strings.Repeat("c", 40), "ORDINARY_OWNERSHIP_ROUTING_EPOCH": "145", "LOCAL_DATABASE_URL": "postgresql://private:do-not-log@postgres:5432/jobseek", "REDIS_URL": "redis://private:do-not-log@redis:6379/0",
	}
}
func TestRuntimeEnvironmentExactIdentityAndFrozenDefaults(t *testing.T) {
	env := fixtureRuntimeEnvironment()
	c, err := ReadRuntimeConfig(func(k string) string { return env[k] }, strings.Repeat("a", 40))
	if err != nil {
		t.Fatal(err)
	}
	if c.rendered || c.concurrency != 5 || c.leaseTTL != 600*time.Second || c.heartbeat != 120*time.Second || c.shutdownGrace != 30*time.Second || c.taskTimeout != 600*time.Second || c.delay != 2 || c.maxDomains != 10 || c.metricsAddress != "127.0.0.1:9104" || c.dataDirectory != "/app/data" {
		t.Fatal("native defaults departed from installed admission contract")
	}
	hash := sha256.Sum256(pinnedCA)
	if hex.EncodeToString(hash[:]) != pinnedCASHA256 {
		t.Fatal("pinned certifi snapshot changed")
	}
	env["ORDINARY_OWNERSHIP_SOURCE_REVISION"] = strings.Repeat("d", 40)
	if c.source != strings.Repeat("a", 40) {
		t.Fatal("startup identity was mutable")
	}
}

func TestRuntimeRenderedDetailsRequiresExplicitProtectedMode(t *testing.T) {
	for _, mode := range []string{"", "enabled", "true", "disabled"} {
		env := fixtureRuntimeEnvironment()
		env["ORDINARY_GO_RENDERED_DETAILS"] = mode
		c, err := ReadRuntimeConfig(func(k string) string { return env[k] }, strings.Repeat("a", 40))
		if mode == "" || mode == "enabled" {
			if err != nil || c.rendered != (mode == "enabled") {
				t.Fatal("protected renderer selection differs", err)
			}
		} else if err != ErrStartup {
			t.Fatal("ambiguous renderer mode accepted")
		}
	}
}
func TestRuntimeEnvironmentRejectsMissingMalformedAndUnsafeBounds(t *testing.T) {
	bad := map[string][]string{
		"ORDINARY_GO_WORKER_MODE": {"", "true", "disabled"}, "ORDINARY_OWNERSHIP_SOURCE_REVISION": {"", strings.Repeat("A", 40), strings.Repeat("d", 40)}, "ORDINARY_OWNERSHIP_PLAN_SHA256": {"", strings.Repeat("b", 63)}, "ORDINARY_OWNERSHIP_PROJECTION_SHA1": {"", strings.Repeat("c", 41)}, "ORDINARY_OWNERSHIP_ROUTING_EPOCH": {"", "0145", "+145", "0", "10000000000000", "145.0"}, "LOCAL_DATABASE_URL": {"", "invalid"}, "REDIS_URL": {"", "invalid"}, "MONITOR_CONCURRENCY": {"-1", "257"}, "DISCOVERY_CONCURRENCY": {"0", "257", "020"}, "INFLIGHT_LEASE_TTL_SECONDS": {"120", "0"}, "INFLIGHT_HEARTBEAT_INTERVAL_SECONDS": {"599", "0"}, "SHUTDOWN_GRACE_SECONDS": {"46", "-1"}, "ORDINARY_GO_CANCELLATION_GRACE_SECONDS": {"0", "11"}, "ORDINARY_GO_TASK_TIMEOUT_SECONDS": {"0", "86401"}, "THROTTLE_DELAY_DEFAULT": {"NaN", "Inf", "-1"}, "ORDINARY_GO_METRICS_ADDRESS": {"localhost:9104", "127.0.0.1:0", "127.0.0.1:65536"}, "INTERNAL_HOSTS_ALLOW": {"https://example.com", "one,,two", "user@example.com", "fe80::1%eth0"}, "WEBSHARE_PROXY_URLS": {"invalid", "{}"}, "ORDINARY_GO_DATA_DIRECTORY": {"relative", "/path\x00secret"},
	}
	for key, values := range bad {
		for _, value := range values {
			t.Run(key+"/"+strconvTestIndex(values, value), func(t *testing.T) {
				env := fixtureRuntimeEnvironment()
				env[key] = value
				_, err := ReadRuntimeConfig(func(k string) string { return env[k] }, strings.Repeat("a", 40))
				if err != ErrStartup || strings.Contains(err.Error(), "do-not-log") {
					t.Fatal("unsafe startup accepted or exposed credentials")
				}
			})
		}
	}
	if _, err := ReadRuntimeConfig(nil, strings.Repeat("a", 40)); err != ErrStartup {
		t.Fatal("nil startup accepted")
	}
}
func strconvTestIndex(values []string, value string) string {
	for i, item := range values {
		if item == value {
			return string(rune('a' + i))
		}
	}
	return "unknown"
}
func TestRuntimeProtectedHostsAndConcurrency(t *testing.T) {
	env := fixtureRuntimeEnvironment()
	env["INTERNAL_HOSTS_ALLOW"] = " Example.COM:8080, [::1]:8108, [2001:db8::1] "
	env["WEBSHARE_PROXY_URLS"] = `["http://secret:private@proxy-a:8080"]`
	env["WEBSHARE_PROXY_URL"] = "http://proxy-b:8080"
	env["TYPESENSE_HOST"] = "typesense:8108"
	env["MONITOR_CONCURRENCY"] = "0"
	env["DISCOVERY_CONCURRENCY"] = "9"
	c, err := ReadRuntimeConfig(func(k string) string { return env[k] }, strings.Repeat("a", 40))
	if err != nil {
		t.Fatal(err)
	}
	if c.concurrency != 9 {
		t.Fatal("unlimited legacy monitor setting did not retain bounded discovery cap")
	}
	got := map[string]bool{}
	for _, host := range c.internalHosts {
		got[host] = true
	}
	for _, host := range []string{"postgres", "redis", "example.com", "::1", "2001:db8::1", "proxy-a", "proxy-b", "typesense"} {
		if !got[host] {
			t.Fatalf("protected host derivation lost %q", host)
		}
	}
	if len(got) != 8 {
		t.Fatal("credential, port or unexpected internal authority leaked")
	}
}
