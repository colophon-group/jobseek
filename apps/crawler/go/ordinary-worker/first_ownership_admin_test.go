package worker

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

func firstRequestFixture(t *testing.T) (FirstOwnershipRequest, map[string]string) {
	t.Helper()
	r := FirstOwnershipRequest{Version: "jobseek.ordinary.first-owner-request/v1", Operation: "activate", SourceRevision: strings.Repeat("a", 40), RoutingEpoch: 147, PlanSHA256: strings.Repeat("b", 64), ProjectionSHA1: strings.Repeat("c", 40), CrawlerImageRef: "ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("d", 64), B0ReceiptSHA256: strings.Repeat("e", 64), B0Cohort: "cdom", Namespace: "production-b0", ShardID: "lightpanda-b0", ColdHostSHA256: strings.Repeat("f", 64)}
	body, err := json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(t.TempDir(), "request.json")
	if err := os.WriteFile(file, append(body, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
	return r, map[string]string{
		"ORDINARY_GO_WORKER_MODE": "activate-first-ownership", "ORDINARY_FIRST_OWNERSHIP_REQUEST_FILE": file,
		"LOCAL_DATABASE_URL": "postgresql://fixture.invalid/unused", "REDIS_URL": "redis://fixture.invalid/unused",
		"ORDINARY_OWNERSHIP_SOURCE_REVISION": r.SourceRevision, "ORDINARY_OWNERSHIP_ROUTING_EPOCH": strconv.FormatInt(r.RoutingEpoch, 10),
		"ORDINARY_OWNERSHIP_PLAN_SHA256": r.PlanSHA256, "ORDINARY_OWNERSHIP_PROJECTION_SHA1": r.ProjectionSHA1,
		"CRAWLER_IMAGE_REF": r.CrawlerImageRef, "LIGHTPANDA_B0_ROUTING_EPOCH": strconv.FormatInt(r.RoutingEpoch, 10),
		"LIGHTPANDA_B0_QUEUE_NAMESPACE": r.Namespace, "LIGHTPANDA_B0_SHARD_ID": r.ShardID, "LIGHTPANDA_B0_PRODUCER_COHORT": r.B0Cohort,
	}
}

func TestReadFirstOwnershipRequiresProtectedExactRequest(t *testing.T) {
	r, env := firstRequestFixture(t)
	get := func(k string) string { return env[k] }
	if c, err := ReadFirstOwnershipAdminConfig(get, r.SourceRevision, "activate"); err != nil || c.request != r || !planPattern.MatchString(c.digest) {
		t.Fatal("exact protected request rejected", err)
	}
	for _, key := range []string{"ORDINARY_GO_WORKER_MODE", "ORDINARY_OWNERSHIP_SOURCE_REVISION", "ORDINARY_OWNERSHIP_PLAN_SHA256", "ORDINARY_OWNERSHIP_PROJECTION_SHA1", "ORDINARY_OWNERSHIP_ROUTING_EPOCH", "CRAWLER_IMAGE_REF", "LIGHTPANDA_B0_ROUTING_EPOCH", "LIGHTPANDA_B0_QUEUE_NAMESPACE", "LIGHTPANDA_B0_SHARD_ID", "LIGHTPANDA_B0_PRODUCER_COHORT"} {
		old := env[key]
		env[key] = "wrong"
		if _, err := ReadFirstOwnershipAdminConfig(get, r.SourceRevision, "activate"); err != ErrStartup {
			t.Fatal("changed request binding admitted", key)
		}
		env[key] = old
	}
	file := env["ORDINARY_FIRST_OWNERSHIP_REQUEST_FILE"]
	if err := os.Chmod(file, 0o666); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadFirstOwnershipAdminConfig(get, r.SourceRevision, "activate"); err != ErrStartup {
		t.Fatal("writable request admitted")
	}
	if err := os.Chmod(file, 0o600); err != nil {
		t.Fatal(err)
	}
	body, _ := json.Marshal(r)
	for _, raw := range []string{string(body) + " {}", strings.Replace(string(body), `"operation":"activate"`, `"operation":"retire","operation":"activate"`, 1), strings.Replace(string(body), `"version":`, `"unexpected":"secret","version":`, 1)} {
		if err := os.WriteFile(file, []byte(raw), 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := ReadFirstOwnershipAdminConfig(get, r.SourceRevision, "activate"); err != ErrStartup || strings.Contains(err.Error(), "secret") {
			t.Fatal("noncanonical or unknown request admitted/exposed")
		}
	}
}

func TestReadFirstOwnershipCompatibilityAdminOnlyRetiresExactOutgoingOwner(t *testing.T) {
	r, env := firstRequestFixture(t)
	installed := strings.Repeat("1", 40)
	image := "ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("2", 64)
	write := func() {
		t.Helper()
		body, _ := json.Marshal(r)
		if err := os.WriteFile(env["ORDINARY_FIRST_OWNERSHIP_REQUEST_FILE"], append(body, '\n'), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	get := func(k string) string { return env[k] }
	if _, err := ReadFirstOwnershipAdminConfig(get, installed, "activate"); err != ErrStartup {
		t.Fatal("foreign activation accepted")
	}
	env["ORDINARY_RETIRE_ADMIN_SOURCE_REVISION"], env["ORDINARY_RETIRE_ADMIN_IMAGE_REF"] = installed, image
	if _, err := ReadFirstOwnershipAdminConfig(get, installed, "activate"); err != ErrStartup {
		t.Fatal("compatibility admin activated old owner")
	}
	r.Operation = "retire"
	write()
	env["ORDINARY_GO_WORKER_MODE"] = "retire-first-ownership"
	c, err := ReadFirstOwnershipAdminConfig(get, installed, "retire")
	if err != nil || c.request.SourceRevision != r.SourceRevision || c.request.CrawlerImageRef != r.CrawlerImageRef || c.adminSource != installed || c.adminImage != image {
		t.Fatal("explicit outgoing/caller identities lost", err)
	}
	for _, key := range []string{"ORDINARY_RETIRE_ADMIN_SOURCE_REVISION", "ORDINARY_RETIRE_ADMIN_IMAGE_REF", "ORDINARY_OWNERSHIP_SOURCE_REVISION", "CRAWLER_IMAGE_REF"} {
		value := env[key]
		env[key] = ""
		if _, err := ReadFirstOwnershipAdminConfig(get, installed, "retire"); err != ErrStartup {
			t.Fatal("missing compatibility binding accepted", key)
		}
		env[key] = value
	}
	env["ORDINARY_RETIRE_ADMIN_IMAGE_REF"] = r.CrawlerImageRef
	if _, err := ReadFirstOwnershipAdminConfig(get, installed, "retire"); err != ErrStartup {
		t.Fatal("outgoing image disguised as compatibility admin")
	}
	env["ORDINARY_RETIRE_ADMIN_IMAGE_REF"] = image
	if _, err := ReadFirstOwnershipAdminConfig(get, r.SourceRevision, "retire"); err != ErrStartup {
		t.Fatal("caller revision taken from environment instead of compiled image")
	}
}
