package releaseevidence

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func executionFixture(t *testing.T) (imageFixture, map[string]any) {
	t.Helper()
	f := imageSetup(t, false)
	ref := crawlerContainerImage().Reference
	f.compose = []byte(`{"name":"jobseek","services":{"worker":{"image":"` + ref + `","entrypoint":["/app/start"],"command":["crawler","native"],"user":"10001:10001","working_dir":"/app/native","environment":{"TOKEN":"fixture-sensitive-value"},"volumes":[{"type":"bind","source":"/private/fixture","target":"/etc/fixture","read_only":true},{"type":"volume","source":"producer","target":"/run/socket","read_only":true}],"tmpfs":["/run/cache:rw,noexec,size=64k"]}},"volumes":{"producer":{"name":"jobseek-producer"}}}`)
	f.inspect = []byte(`[{"Id":"` + fixtureImageID + `","RepoDigests":["` + ref + `"],"Architecture":"amd64","Os":"linux","Config":{"User":"","WorkingDir":"/app","Cmd":["crawler","run"],"Entrypoint":["/app/start"],"Env":["BASE=fixture-sensitive-value","PATH=/bin"],"Volumes":{"/data":{}}}}]`)
	c := containerFixture(1, "jobseek", "worker")
	config := c["Config"].(map[string]any)
	config["User"], config["WorkingDir"], config["Cmd"], config["Entrypoint"], config["Env"] = "10001:10001", "/app/native", []string{"crawler", "native"}, []string{"/app/start"}, []string{"TOKEN=fixture-sensitive-value", "PATH=/bin", "BASE=fixture-sensitive-value"}
	c["Mounts"] = []any{
		map[string]any{"Type": "bind", "Source": "/private/fixture", "Destination": "/etc/fixture", "RW": false, "Propagation": "rprivate"},
		map[string]any{"Type": "volume", "Name": "jobseek-producer", "Source": "/var/lib/docker/volumes/jobseek-producer/_data", "Destination": "/run/socket", "RW": false, "Driver": "local"},
		map[string]any{"Type": "volume", "Name": "anonymous-data", "Source": "/var/lib/docker/volumes/anonymous-data/_data", "Destination": "/data", "RW": true, "Driver": "local"},
	}
	c["HostConfig"].(map[string]any)["Tmpfs"] = map[string]string{"/run/cache": "rw,noexec,size=64k"}
	c["HostConfig"].(map[string]any)["Binds"] = nil
	c["HostConfig"].(map[string]any)["VolumesFrom"] = nil
	c["HostConfig"].(map[string]any)["VolumeDriver"] = ""
	return f, c
}

func executionProof(t *testing.T, f imageFixture) *Images {
	t.Helper()
	proof, err := observeImages(context.Background(), f.c, f.reader(t))
	if err != nil {
		t.Fatal(err)
	}
	return proof
}

func TestContainerExecutionBindsPrivateComposeDefaultsAndMounts(t *testing.T) {
	f, c := executionFixture(t)
	proof := executionProof(t, f)
	foreign := containerFixture(2, "other", "oneoff")
	got, err := RequireContainerExecution(context.Background(), observeFixtureContainers(t, c, foreign), []*Images{proof})
	if err != nil || got.SHA256() != digest([]byte(got.Body())) {
		t.Fatal("complete release execution settings refused", err)
	}
	if strings.Contains(got.Body(), "fixture-sensitive-value") || strings.Contains(got.Body(), "TOKEN") || strings.Contains(got.Body(), "/var/lib") || strings.Contains(got.Body(), "/private") {
		t.Fatal("private runtime inputs disclosed")
	}
	if !strings.Contains(got.Body(), `"configured_uid":10001`) || !strings.Contains(got.Body(), `"configured_gid":10001`) || !strings.Contains(got.Body(), `"unaccounted_containers":1`) || !strings.Contains(got.Body(), proof.SHA256()) {
		t.Fatal("incomplete declared execution binding")
	}
	for _, mutation := range []string{"command", "entrypoint", "user", "user-name", "partial-user", "working-directory", "missing-env", "extra-env", "wrong-env", "duplicate-env", "bind-source", "bind-access", "bind-propagation", "named-volume", "image-volume-access", "missing-image-volume", "image-volume-driver", "image-volume-substitution", "extra-mount", "duplicate-mount", "tmpfs-options", "extra-tmpfs", "volumes-from", "volume-driver", "pid"} {
		t.Run(mutation, func(t *testing.T) {
			f, c := executionFixture(t)
			cfg := c["Config"].(map[string]any)
			host := c["HostConfig"].(map[string]any)
			mounts := c["Mounts"].([]any)
			switch mutation {
			case "command":
				cfg["Cmd"] = []string{"crawler", "wrong"}
			case "entrypoint":
				cfg["Entrypoint"] = []string{"/bin/sh"}
			case "user":
				cfg["User"] = "0:0"
			case "user-name":
				cfg["User"] = "crawler:crawler"
			case "partial-user":
				cfg["User"] = "10001"
			case "working-directory":
				cfg["WorkingDir"] = "/app/other"
			case "missing-env":
				cfg["Env"] = []string{"TOKEN=fixture-sensitive-value", "PATH=/bin"}
			case "extra-env":
				cfg["Env"] = append(cfg["Env"].([]string), "LD_PRELOAD=fixture-sensitive-value")
			case "wrong-env":
				cfg["Env"] = []string{"TOKEN=other", "PATH=/bin", "BASE=fixture-sensitive-value"}
			case "duplicate-env":
				cfg["Env"] = append(cfg["Env"].([]string), "TOKEN=fixture-sensitive-value")
			case "bind-source":
				mounts[0].(map[string]any)["Source"] = "/private/other"
			case "bind-access":
				mounts[0].(map[string]any)["RW"] = true
			case "bind-propagation":
				mounts[0].(map[string]any)["Propagation"] = "shared"
			case "named-volume":
				mounts[1].(map[string]any)["Name"] = "other-producer"
			case "image-volume-access":
				mounts[2].(map[string]any)["RW"] = false
			case "missing-image-volume":
				c["Mounts"] = mounts[:2]
			case "image-volume-driver":
				mounts[2].(map[string]any)["Driver"] = "remote"
			case "image-volume-substitution":
				host["Binds"] = []string{"/private/other:/data"}
			case "extra-mount":
				c["Mounts"] = append(mounts, map[string]any{"Type": "bind", "Source": "/private/code", "Destination": "/app/start", "RW": false})
			case "duplicate-mount":
				c["Mounts"] = append(mounts, mounts[0])
			case "tmpfs-options":
				host["Tmpfs"] = map[string]string{"/run/cache": "rw,exec,size=64k"}
			case "extra-tmpfs":
				host["Tmpfs"] = map[string]string{"/run/cache": "rw,noexec,size=64k", "/app": "rw"}
			case "volumes-from":
				host["VolumesFrom"] = []string{"foreign"}
			case "volume-driver":
				host["VolumeDriver"] = "remote"
			case "pid":
				delete(c["State"].(map[string]any), "Pid")
			}
			inventory, err := observeContainers(context.Background(), containerReader(t, c))
			if err == nil {
				_, err = RequireContainerExecution(context.Background(), inventory, []*Images{executionProof(t, f)})
			}
			if !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), "fixture-sensitive-value") {
				t.Fatal("execution drift accepted or disclosed", err)
			}
		})
	}
}

func TestContainerExecutionRejectsUnsupportedDeclaredSourcesAndIncompleteEvidence(t *testing.T) {
	for _, mutation := range []string{"null-env", "unresolved-env", "shell-command", "config", "secret", "volumes-from", "volume-subpath", "missing-volume-name", "mutable-defaults", "missing-private-compose", "private-compose-drift", "missing-image-config", "image-defaults-drift", "wrong-image", "unknown-service", "active-oneoff", "restartable-oneoff"} {
		t.Run(mutation, func(t *testing.T) {
			f, c := executionFixture(t)
			switch mutation {
			case "null-env":
				f.compose = []byte(strings.Replace(string(f.compose), `"TOKEN":"fixture-sensitive-value"`, `"TOKEN":null`, 1))
			case "unresolved-env":
				f.compose = []byte(strings.Replace(string(f.compose), `"TOKEN":"fixture-sensitive-value"`, `"TOKEN":"fixture-sensitive-value","BAD-NAME":"value"`, 1))
			case "shell-command":
				f.compose = []byte(strings.Replace(string(f.compose), `["crawler","native"]`, `"crawler native"`, 1))
			case "config":
				f.compose = []byte(strings.Replace(string(f.compose), `"user":`, `"configs":["unverified"],"user":`, 1))
			case "secret":
				f.compose = []byte(strings.Replace(string(f.compose), `"user":`, `"secrets":["unverified"],"user":`, 1))
			case "volumes-from":
				f.compose = []byte(strings.Replace(string(f.compose), `"user":`, `"volumes_from":["foreign"],"user":`, 1))
			case "volume-subpath":
				f.compose = []byte(strings.Replace(string(f.compose), `"source":"producer"`, `"source":"producer","volume":{"subpath":"other"}`, 1))
			case "missing-volume-name":
				f.compose = []byte(strings.Replace(string(f.compose), `"name":"jobseek-producer"`, `"name":""`, 1))
			case "mutable-defaults":
				f.inspect = []byte(strings.Replace(string(f.inspect), `"BASE=fixture-sensitive-value"`, `"BASE=fixture-sensitive-value","BASE=other"`, 1))
			case "missing-image-config":
				f.inspect = []byte(strings.Replace(string(f.inspect), `"Config":{`, `"NotConfig":{`, 1))
			case "image-defaults-drift":
				f.inspect = []byte(strings.Replace(string(f.inspect), `"PATH=/bin"`, `"PATH=/other"`, 1))
			case "wrong-image":
				c["Image"] = "sha256:" + strings.Repeat("2", 64)
			case "unknown-service":
				c["Config"].(map[string]any)["Labels"].(map[string]any)["com.docker.compose.service"] = "unknown"
			case "active-oneoff":
				c["Config"].(map[string]any)["Labels"].(map[string]any)["com.docker.compose.oneoff"] = "True"
				c["State"].(map[string]any)["Running"] = true
			case "restartable-oneoff":
				c["Config"].(map[string]any)["Labels"].(map[string]any)["com.docker.compose.oneoff"] = "True"
				c["HostConfig"].(map[string]any)["RestartPolicy"].(map[string]any)["Name"] = "always"
			}
			proof := executionProof(t, f)
			if mutation == "missing-private-compose" {
				proof.compose = nil
			}
			if mutation == "private-compose-drift" {
				proof.compose = append(proof.compose, ' ')
			}
			_, err := RequireContainerExecution(context.Background(), observeFixtureContainers(t, c), []*Images{proof})
			if !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), "fixture-sensitive-value") {
				t.Fatal("unsupported or incomplete execution evidence accepted", err)
			}
		})
	}
	f, c := executionFixture(t)
	proof := executionProof(t, f)
	inventory := observeFixtureContainers(t, c)
	for _, proofs := range [][]*Images{nil, {nil}, {proof, proof}, {fixtureContainerImages(t, map[string]serviceImage{"worker": crawlerContainerImage()})}} {
		if _, err := RequireContainerExecution(context.Background(), inventory, proofs); !errors.Is(err, ErrInvalid) {
			t.Fatal("incomplete opaque execution evidence admitted", err)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := RequireContainerExecution(ctx, inventory, []*Images{proof}); !errors.Is(err, ErrInvalid) {
		t.Fatal("cancelled execution proof admitted", err)
	}
}

func TestExecutionDefaultsClearEntrypointAndRequireExactNumericUsers(t *testing.T) {
	for _, input := range []string{"0:0", "10001:10001", "4294967295:4294967295", ""} {
		if _, _, err := configuredUser(input); err != nil {
			t.Fatal(err)
		}
	}
	for _, input := range []string{"root", "10001", "-1:0", "01:0", "4294967296:0", "0:4294967296"} {
		if _, _, err := configuredUser(input); !errors.Is(err, ErrInvalid) {
			t.Fatal("ambiguous user admitted", input)
		}
	}
	for _, command := range []string{`[]`, `""`} {
		f, c := executionFixture(t)
		var d map[string]any
		_ = json.Unmarshal(f.compose, &d)
		s := d["services"].(map[string]any)["worker"].(map[string]any)
		delete(s, "command")
		s["entrypoint"] = json.RawMessage(command)
		f.compose, _ = json.Marshal(d)
		cfg := c["Config"].(map[string]any)
		cfg["Cmd"], cfg["Entrypoint"] = []string{}, []string{}
		if _, err := RequireContainerExecution(context.Background(), observeFixtureContainers(t, c), []*Images{executionProof(t, f)}); err != nil {
			t.Fatal("explicit entrypoint clear inherited image CMD", err)
		}
	}
}

func TestExecutionReceiptIsStableAcrossEquivalentStagedReleaseOrdering(t *testing.T) {
	f, c := executionFixture(t)
	first := executionProof(t, f)
	f.compose = append(f.compose, ' ')
	second := executionProof(t, f)
	inventory := observeFixtureContainers(t, c)
	a, err := RequireContainerExecution(context.Background(), inventory, []*Images{first, second})
	if err != nil {
		t.Fatal(err)
	}
	b, err := RequireContainerExecution(context.Background(), inventory, []*Images{second, first})
	if err != nil || a.SHA256() != b.SHA256() {
		t.Fatal("receipt depends on caller release order", err)
	}
	oneoff := containerFixture(2, "jobseek", "worker")
	oneoff["Config"].(map[string]any)["Labels"].(map[string]any)["com.docker.compose.oneoff"] = "True"
	got, err := RequireContainerExecution(context.Background(), observeFixtureContainers(t, c, oneoff), []*Images{first})
	if err != nil || !strings.Contains(got.Body(), `"contained_oneoffs":1`) {
		t.Fatal("contained oneoff cannot be retained without runtime grant", err)
	}
	if !strings.Contains(got.Body(), `"runtime_admission":false`) {
		t.Fatal("declared execution settings became an admission grant")
	}
}

func TestExecutionRejectsMissingStableEngineFieldsAndDeclaredVolumeOptions(t *testing.T) {
	for _, field := range []string{"User", "WorkingDir", "Env", "Cmd", "Entrypoint", "Binds", "VolumesFrom", "VolumeDriver"} {
		t.Run(field, func(t *testing.T) {
			f, c := executionFixture(t)
			cfg := c["Config"].(map[string]any)
			if field == "Binds" || field == "VolumesFrom" || field == "VolumeDriver" {
				cfg = c["HostConfig"].(map[string]any)
			}
			delete(cfg, field)
			if _, err := RequireContainerExecution(context.Background(), observeFixtureContainers(t, c), []*Images{executionProof(t, f)}); !errors.Is(err, ErrInvalid) {
				t.Fatal("missing evidence became an empty default", err)
			}
		})
	}
	for _, definition := range []string{`"name":"jobseek-producer","driver":"remote"`, `"name":"jobseek-producer","driver_opts":{"device":"/private/other"}`} {
		f, c := executionFixture(t)
		f.compose = []byte(strings.Replace(string(f.compose), `"name":"jobseek-producer"`, definition, 1))
		if _, err := RequireContainerExecution(context.Background(), observeFixtureContainers(t, c), []*Images{executionProof(t, f)}); !errors.Is(err, ErrInvalid) {
			t.Fatal("unverified volume provider admitted", err)
		}
	}
	if _, err := decodeExecutionConfig([]byte(`{"User":null}`), false); !errors.Is(err, ErrInvalid) {
		t.Fatal("explicit null image user became root", err)
	}
	if _, err := decodeExecutionConfig([]byte(`{}`), false); err != nil {
		t.Fatal("optional OCI image defaults rejected", err)
	}
	if _, err := decodeExecutionConfig([]byte(`{"User":"10001:10001","USER":""}`), false); !errors.Is(err, ErrInvalid) {
		t.Fatal("case alias shadowed configured user", err)
	}
}
