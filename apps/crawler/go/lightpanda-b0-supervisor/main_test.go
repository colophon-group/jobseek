package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestDarkConfigReadsNoAuthorityConfiguration(t *testing.T) {
	t.Setenv("LIGHTPANDA_B0_SUPERVISOR_MODE", "dark")
	t.Setenv("REDIS_URL", "not-a-redis-url")
	t.Setenv("LIGHTPANDA_B0_ROUTING_EPOCH", "not-an-integer")
	configured, err := configFromEnvironment()
	if err != nil || configured.Mode != modeDark || configured.RedisOptions != nil {
		t.Fatalf("dark configuration acquired authority: config=%+v err=%v", configured, err)
	}
}

func TestEnabledHealthQueriesLiveReadiness(t *testing.T) {
	ready := false
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/healthz" {
			http.NotFound(response, request)
			return
		}
		if !ready {
			http.Error(response, "not ready", http.StatusServiceUnavailable)
			return
		}
		response.WriteHeader(http.StatusNoContent)
	}))
	t.Cleanup(server.Close)
	address := server.Listener.Addr().String()
	if err := checkEnabledReady(address); err == nil {
		t.Fatal("unready enabled supervisor was healthy")
	}
	ready = true
	if err := checkEnabledReady(address); err != nil {
		t.Fatalf("ready enabled supervisor was unhealthy: %v", err)
	}
}

func TestDarkReadinessMarkerHasExactHealthContract(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ready")
	if err := publishDarkReady(path); err != nil {
		t.Fatal(err)
	}
	if err := checkDarkReady(path); err != nil {
		t.Fatalf("fresh marker is unhealthy: %v", err)
	}
	if err := os.Chmod(path, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := checkDarkReady(path); err == nil {
		t.Fatal("world-readable readiness marker was accepted")
	}
	if err := publishDarkReady(path); err == nil {
		t.Fatal("existing readiness marker was overwritten")
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(t.TempDir(), "target")
	if err := os.WriteFile(target, []byte(darkReadyContent), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, path); err != nil {
		t.Fatal(err)
	}
	if err := checkDarkReady(path); err == nil {
		t.Fatal("symlink readiness marker was accepted")
	}
}
