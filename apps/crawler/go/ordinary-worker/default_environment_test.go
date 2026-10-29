package worker

import (
	"context"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestDefaultRuntimeHealthRequiresDefaultIdentity(t *testing.T) {
	m := newRuntimeMetrics(1, time.Minute)
	m.source = strings.Repeat("a", 40)
	m.epoch = 145
	m.defaultOwner = true
	m.ready.Store(true)
	server := httptest.NewServer(m)
	defer server.Close()
	c := RuntimeConfig{defaultOwner: true, rendered: true, source: m.source, epoch: m.epoch, metricsAddress: strings.TrimPrefix(server.URL, "http://")}
	if err := CheckHealth(context.Background(), c); err != nil {
		t.Fatal("default health rejected running default owner", err)
	}
	m.defaultOwner = false
	if err := CheckHealth(context.Background(), c); err == nil {
		t.Fatal("default health adopted another runtime mode")
	}
	m.defaultOwner = true
	m.epoch++
	if err := CheckHealth(context.Background(), c); err == nil {
		t.Fatal("default health adopted another routing epoch")
	}
}

func defaultFixtureEnvironment() map[string]string {
	env := fixtureRuntimeEnvironment()
	env["ORDINARY_GO_WORKER_MODE"] = "default"
	env["ORDINARY_GO_RENDERED_DETAILS"] = "enabled"
	for _, k := range []string{"ORDINARY_OWNERSHIP_SOURCE_REVISION", "ORDINARY_OWNERSHIP_PLAN_SHA256", "ORDINARY_OWNERSHIP_PROJECTION_SHA1"} {
		delete(env, k)
	}
	return env
}
func TestDefaultRuntimeUsesInstalledSourceAndExplicitEpoch(t *testing.T) {
	env := defaultFixtureEnvironment()
	c, err := ReadRuntimeConfig(func(k string) string { return env[k] }, strings.Repeat("a", 40))
	if err != nil || !c.defaultOwner || !c.rendered || c.source != strings.Repeat("a", 40) || c.plan != "" || c.projection != "" || c.epoch != 145 || c.concurrency != 20 || c.monitorConcurrency != 5 {
		t.Fatal("default runtime authority or concurrency changed", err)
	}
	for _, key := range []string{"ORDINARY_OWNERSHIP_SOURCE_REVISION", "ORDINARY_OWNERSHIP_PLAN_SHA256", "ORDINARY_OWNERSHIP_PROJECTION_SHA1", "ORDINARY_OWNERSHIP_ROUTING_EPOCH", "ORDINARY_GO_RENDERED_DETAILS"} {
		t.Run(key, func(t *testing.T) {
			env := defaultFixtureEnvironment()
			env[key] = ""
			if key == "ORDINARY_OWNERSHIP_SOURCE_REVISION" {
				env[key] = strings.Repeat("a", 40)
			}
			if key == "ORDINARY_OWNERSHIP_PLAN_SHA256" {
				env[key] = strings.Repeat("b", 64)
			}
			if key == "ORDINARY_OWNERSHIP_PROJECTION_SHA1" {
				env[key] = strings.Repeat("c", 40)
			}
			if _, err := ReadRuntimeConfig(func(k string) string { return env[k] }, strings.Repeat("a", 40)); err != ErrStartup {
				t.Fatal("default mode adopted stale migration selectors or missing epoch/renderer")
			}
		})
	}
}
