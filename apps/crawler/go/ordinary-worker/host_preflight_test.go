package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func hostPrivateDirectory(t *testing.T) string {
	t.Helper()
	p, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil || os.Chmod(p, 0700) != nil {
		t.Fatal("private host fixture directory")
	}
	return p
}

func hostRequestFixture() HostPreflightRequest {
	r := HostPreflightRequest{Version: "jobseek.crawler-host-preflight-request/v1", CoordinatorSource: strings.Repeat("a", 40), Owner: "colophon-group", Project: "jobseek", Architecture: "amd64", DeploymentDirectory: "/private/deployment", Installed: []HostInstalledRequest{}}
	for _, role := range []string{"active", "incoming", "rollback"} {
		r.Releases = append(r.Releases, HostReleaseRequest{role, "/private/" + role, strings.Repeat("b", 64)})
	}
	return r
}

func TestHostPreflightRequiresCanonicalBoundThreeReleaseRequest(t *testing.T) {
	r := hostRequestFixture()
	body, _ := json.Marshal(r)
	if _, err := decodeHostRequest(body, hostDigest(body), r.CoordinatorSource); err != nil {
		t.Fatal("canonical host request refused")
	}
	for _, fault := range []string{"hash", "source", "owner", "project", "platform", "deployment alias", "relative release", "missing rollback", "role order", "missing installed set", "unknown", "duplicate", "case alias", "trailer"} {
		t.Run(fault, func(t *testing.T) {
			x := hostRequestFixture()
			switch fault {
			case "source":
				x.CoordinatorSource = strings.Repeat("c", 40)
			case "owner":
				x.Owner = "operator/escape"
			case "project":
				x.Project = "--option"
			case "platform":
				x.Architecture = "x86"
			case "deployment alias":
				x.DeploymentDirectory = "/private/../deployment"
			case "relative release":
				x.Releases[1].Directory = "incoming"
			case "missing rollback":
				x.Releases = x.Releases[:2]
			case "role order":
				x.Releases[0], x.Releases[1] = x.Releases[1], x.Releases[0]
			case "missing installed set":
				x.Installed = nil
			}
			b, _ := json.Marshal(x)
			switch fault {
			case "unknown":
				b = []byte(strings.Replace(string(b), `{`, `{"credential":"hidden",`, 1))
			case "duplicate":
				b = []byte(strings.Replace(string(b), `{`, `{"version":"ignored",`, 1))
			case "case alias":
				b = []byte(strings.Replace(string(b), `"project"`, `"Project"`, 1))
			case "trailer":
				b = append(b, '\n')
			}
			hash := hostDigest(b)
			if fault == "hash" {
				hash = strings.Repeat("c", 64)
			}
			if _, err := decodeHostRequest(b, hash, r.CoordinatorSource); !errors.Is(err, errHostPreflight) || strings.Contains(err.Error(), "hidden") {
				t.Fatal("unsafe host request admitted/disclosed", err)
			}
		})
	}
}

func TestHostPreflightConfigRequiresExplicitCompiledSourceAndMode(t *testing.T) {
	r := hostRequestFixture()
	env := map[string]string{"ORDINARY_GO_WORKER_MODE": "host-preflight", "ORDINARY_HOST_COORDINATOR_SOURCE_REVISION": r.CoordinatorSource, "ORDINARY_HOST_REQUEST_DIRECTORY": "/private/request", "ORDINARY_HOST_REQUEST_SHA256": strings.Repeat("b", 64)}
	get := func(k string) string { return env[k] }
	if _, err := ReadHostPreflightConfig(get, r.CoordinatorSource); err != nil {
		t.Fatal("explicit host mode refused")
	}
	for _, key := range []string{"ORDINARY_GO_WORKER_MODE", "ORDINARY_HOST_COORDINATOR_SOURCE_REVISION", "ORDINARY_HOST_REQUEST_DIRECTORY", "ORDINARY_HOST_REQUEST_SHA256"} {
		old := env[key]
		env[key] = ""
		if _, err := ReadHostPreflightConfig(get, r.CoordinatorSource); !errors.Is(err, errHostPreflight) {
			t.Fatal("implicit host mode admitted", key)
		}
		env[key] = old
	}
}

func TestHostMutationLockRejectsContentionReplacementAndAliases(t *testing.T) {
	path := filepath.Join(hostPrivateDirectory(t), "mutation.lock")
	lock, err := acquireHostLock(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Millisecond)
	defer cancel()
	if other, err := acquireHostLock(ctx, path); err == nil || other != nil {
		t.Fatal("concurrent mutation lock admitted")
	}
	alias := filepath.Join(filepath.Dir(path), "alias.lock")
	if os.Symlink(path, alias) != nil {
		t.Fatal("lock alias fixture")
	}
	if other, err := acquireHostLock(context.Background(), alias); err == nil || other != nil {
		t.Fatal("symlink mutation lock admitted")
	}
	if os.Remove(path) != nil || os.WriteFile(path, []byte{}, 0600) != nil {
		t.Fatal("lock replacement fixture")
	}
	if lock.verify() == nil {
		t.Fatal("replacement lock inode adopted")
	}
}

func TestHostStoreRetainsImmutablePrivateBytesAndRejectsDrift(t *testing.T) {
	path := hostPrivateDirectory(t)
	s, err := openHostStore(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	body := []byte(`{"protected":"fixture"}`)
	if s.retain("intent.json", body, nil) != nil || s.retain("intent.json", body, nil) != nil {
		t.Fatal("durable exact retry refused")
	}
	if s.retain("intent.json", []byte("changed"), nil) == nil {
		t.Fatal("retained intent overwritten")
	}
	got, err := s.read("intent.json", false)
	if err != nil || !bytes.Equal(got, body) {
		t.Fatal("retained bytes changed")
	}
	if os.Chmod(filepath.Join(path, "intent.json"), 0644) != nil {
		t.Fatal("mode fixture")
	}
	if _, err := s.read("intent.json", false); err == nil {
		t.Fatal("nonprivate retained intent admitted")
	}
	if os.Symlink("intent.json", filepath.Join(path, "request.json")) != nil {
		t.Fatal("request symlink fixture")
	}
	if _, err := s.read("request.json", false); err == nil {
		t.Fatal("request alias admitted")
	}
}

func TestHostStoreRejectsUnexplainedHardLinksAndDirectoryReplacement(t *testing.T) {
	path := hostPrivateDirectory(t)
	s, err := openHostStore(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	body := []byte("retained-intent")
	if s.retain("intent.json", body, nil) != nil || s.root.Link("intent.json", "unexpected-link.json") != nil {
		t.Fatal("hard-link refusal fixture")
	}
	if s.retain("intent.json", body, nil) == nil {
		t.Fatal("unexplained second link adopted as crash recovery")
	}
	if os.Rename(path, path+"-replaced") != nil || os.Mkdir(path, 0700) != nil {
		t.Fatal("directory replacement fixture")
	}
	t.Cleanup(func() { _ = os.RemoveAll(path + "-replaced") })
	if s.verify() == nil {
		t.Fatal("replacement state directory adopted")
	}
}

func TestHostStoreCrashHelper(t *testing.T) {
	if os.Getenv("JOBSEEK_HOST_STORE_TEST_HELPER") != "1" {
		return
	}
	s, err := openHostStore(os.Getenv("JOBSEEK_HOST_STORE_TEST_DIRECTORY"))
	if err != nil {
		os.Exit(2)
	}
	defer s.Close()
	_ = s.retain("intent.json", []byte("retained-intent"), func(phase string) error {
		if phase == os.Getenv("JOBSEEK_HOST_STORE_TEST_CRASH_PHASE") {
			if syscall.Kill(os.Getpid(), syscall.SIGKILL) != nil {
				os.Exit(3)
			}
			select {} // do not race pending signal delivery with a normal exit
		}
		return nil
	})
	os.Exit(4)
}

func TestHostStoreSIGKILLRecoversExclusivePublicationWithoutOverwrite(t *testing.T) {
	for _, phase := range []string{"file_synced", "file_linked"} {
		t.Run(phase, func(t *testing.T) {
			path := hostPrivateDirectory(t)
			cmd := exec.Command(os.Args[0], "-test.run=^TestHostStoreCrashHelper$")
			cmd.Env = append(os.Environ(), "JOBSEEK_HOST_STORE_TEST_HELPER=1", "JOBSEEK_HOST_STORE_TEST_DIRECTORY="+path, "JOBSEEK_HOST_STORE_TEST_CRASH_PHASE="+phase)
			err := cmd.Run()
			var exit *exec.ExitError
			if !errors.As(err, &exit) || !exit.ProcessState.Sys().(syscall.WaitStatus).Signaled() || exit.ProcessState.Sys().(syscall.WaitStatus).Signal() != syscall.SIGKILL {
				t.Fatal("real retention crash was not SIGKILL", err)
			}
			s, err := openHostStore(path)
			if err != nil {
				t.Fatal(err)
			}
			defer s.Close()
			if phase == "file_synced" {
				if _, err := s.read("intent.json", true); !errors.Is(err, fs.ErrNotExist) {
					t.Fatal("unpublished synced temp became intent")
				}
			}
			if s.retain("intent.json", []byte("retained-intent"), nil) != nil {
				t.Fatal("exact crash recovery refused")
			}
			got, err := s.read("intent.json", false)
			if err != nil || string(got) != "retained-intent" {
				t.Fatal("crash recovery bytes/links drifted")
			}
			if s.retain("intent.json", []byte("different-intent"), nil) == nil {
				t.Fatal("crash recovery overwrote original intent")
			}
		})
	}
}
