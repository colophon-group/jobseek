package worker

import (
	"os"
	"strings"
	"testing"
)

func TestHostQuiescenceRequiresExplicitModeAndClearsCallerPGSettings(t *testing.T) {
	source := strings.Repeat("a", 40)
	env := map[string]string{"ORDINARY_GO_WORKER_MODE": "host-quiesce", "ORDINARY_HOST_COORDINATOR_SOURCE_REVISION": source, "ORDINARY_HOST_REQUEST_DIRECTORY": "/private/request", "ORDINARY_HOST_REQUEST_SHA256": strings.Repeat("b", 64), "ORDINARY_HOST_PREFLIGHT_INTENT_SHA256": strings.Repeat("c", 64)}
	get := func(key string) string { return env[key] }
	if _, err := ReadHostQuiescenceConfig(get, source); err != nil {
		t.Fatal("explicit SQL phase refused", err)
	}
	env["ORDINARY_GO_WORKER_MODE"] = "host-contain"
	if _, err := ReadHostQuiescenceConfig(get, source); err == nil {
		t.Fatal("Docker-only phase enabled SQL")
	}
	for _, key := range []string{"PGHOST", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGOPTIONS", "PGSSLCERT", "PGSSLKEY", "PGSSLROOTCERT"} {
		t.Setenv(key, "caller-sensitive-value")
	}
	if ClearHostDatabaseEnvironment() != nil {
		t.Fatal("caller PG environment clearing")
	}
	for _, key := range []string{"PGHOST", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGOPTIONS", "PGSSLCERT", "PGSSLKEY", "PGSSLROOTCERT"} {
		if os.Getenv(key) != "" {
			t.Fatal("caller PG setting retained", key)
		}
	}
}
