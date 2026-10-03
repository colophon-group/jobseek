package worker

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/sys/unix"
)

func TestOwnershipAdminSeparatesProtectedModesAndExactIdentities(t *testing.T) {
	base := map[string]string{"ORDINARY_GO_WORKER_MODE": "stage-ownership", "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("a", 40), "ORDINARY_OWNERSHIP_ROUTING_EPOCH": "145", "LOCAL_DATABASE_URL": "postgresql://private:do-not-log@localhost/fixture", "REDIS_URL": "unix:///private/redis.sock", "ORDINARY_GO_COHORT_FILE": "/private/cohort.json"}
	getenv := func(k string) string { return base[k] }
	if _, err := ReadOwnershipAdminConfig(getenv, strings.Repeat("a", 40), false); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadOwnershipAdminConfig(getenv, strings.Repeat("a", 40), true); err != ErrStartup {
		t.Fatal("staging environment admitted inspection")
	}
	for key, value := range map[string]string{"ORDINARY_GO_WORKER_MODE": "enabled", "ORDINARY_OWNERSHIP_SOURCE_REVISION": strings.Repeat("b", 40), "ORDINARY_OWNERSHIP_ROUTING_EPOCH": "0145", "LOCAL_DATABASE_URL": "", "REDIS_URL": "", "ORDINARY_GO_COHORT_FILE": "relative", "ORDINARY_OWNERSHIP_PLAN_SHA256": strings.Repeat("a", 64), "ORDINARY_OWNERSHIP_PROJECTION_SHA1": strings.Repeat("b", 40)} {
		old := base[key]
		base[key] = value
		if _, err := ReadOwnershipAdminConfig(getenv, strings.Repeat("a", 40), false); err != ErrStartup || strings.Contains(err.Error(), "do-not-log") {
			t.Fatal("unsafe administrative environment accepted", key)
		}
		base[key] = old
	}
	base["ORDINARY_GO_WORKER_MODE"] = "inspect-ownership"
	base["ORDINARY_GO_COHORT_FILE"] = ""
	base["ORDINARY_OWNERSHIP_PLAN_SHA256"] = strings.Repeat("b", 64)
	if _, err := ReadOwnershipAdminConfig(getenv, strings.Repeat("a", 40), true); err != nil {
		t.Fatal(err)
	}
	base["ORDINARY_GO_COHORT_FILE"] = "/private/cohort.json"
	if _, err := ReadOwnershipAdminConfig(getenv, strings.Repeat("a", 40), true); err != ErrStartup {
		t.Fatal("inspection accepted ambiguous cohort input")
	}
}

func TestOwnershipCohortRejectsLinksDevicesUnsafePermissionsAndAmbiguousInputs(t *testing.T) {
	directory := t.TempDir()
	path := filepath.Join(directory, "cohort.json")
	id := "00000000-0000-4000-8000-000000000001"
	for _, input := range []string{"null", "[]", `{ "ids": [] }`, `["secret-connection-string"]`, `["` + id + `","` + id + `"]`, `["` + id + `"] {}`, strings.Repeat(" ", cohortFileLimit+1)} {
		if err := os.WriteFile(path, []byte(input), 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := readOwnershipCohort(path); err != ErrStartup || strings.Contains(err.Error(), "secret") {
			t.Fatal("invalid cohort accepted or exposed input")
		}
	}
	if err := os.WriteFile(path, []byte(`["`+id+`"]`), 0o600); err != nil {
		t.Fatal(err)
	}
	if ids, err := readOwnershipCohort(path); err != nil || len(ids) != 1 || ids[0] != id {
		t.Fatal("valid protected cohort declined", err)
	}
	link := filepath.Join(directory, "cohort-link.json")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	if _, err := readOwnershipCohort(link); err != ErrStartup {
		t.Fatal("symlink cohort followed")
	}
	fifo := filepath.Join(directory, "cohort-fifo")
	if err := unix.Mkfifo(fifo, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := readOwnershipCohort(fifo); err != ErrStartup {
		t.Fatal("FIFO cohort accepted")
	}
	if err := os.Chmod(path, 0o622); err != nil {
		t.Fatal(err)
	}
	if _, err := readOwnershipCohort(path); err != ErrStartup {
		t.Fatal("shared-writable cohort accepted")
	}
}
