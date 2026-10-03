package releaseevidence

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func selectedRedisFixture(t *testing.T) (*Containers, *Images, *Execution) {
	t.Helper()
	worker, server := containerFixture(1, "jobseek", "worker"), containerFixture(2, "jobseek", "redis")
	serverID := "sha256:" + strings.Repeat("2", 64)
	server["Image"] = serverID
	server["State"] = map[string]any{"Running": true, "Paused": false, "Restarting": false, "Pid": 19, "Status": "running"}
	for _, c := range []map[string]any{worker, server} {
		cfg := c["Config"].(map[string]any)
		cfg["User"], cfg["WorkingDir"], cfg["Env"] = "0:0", "/", []string{"BASE=fixture-sensitive-value"}
		h := c["HostConfig"].(map[string]any)
		h["NetworkMode"] = "host"
		h["Binds"] = nil
		h["VolumesFrom"] = nil
		h["VolumeDriver"] = ""
		c["Mounts"] = []any{}
	}
	worker["Config"].(map[string]any)["Cmd"] = []string{"600"}
	worker["Config"].(map[string]any)["Entrypoint"] = []string{"/bin/sleep"}
	worker["Config"].(map[string]any)["Env"] = []string{"BASE=fixture-sensitive-value", "REDIS_URL=redis://localhost:6379/0"}
	server["Config"].(map[string]any)["Cmd"] = []string{"redis-server", "--bind", "127.0.0.1"}
	server["Config"].(map[string]any)["Entrypoint"] = []string{"docker-entrypoint.sh"}
	ref := crawlerContainerImage().Reference
	redisRef := "redis:8-alpine@sha256:" + strings.Repeat("d", 64)
	compose := []byte(`{"name":"jobseek","services":{"worker":{"image":"` + ref + `","network_mode":"host","entrypoint":["/bin/sleep"],"command":["600"],"environment":{"REDIS_URL":"redis://localhost:6379/0"}},"redis":{"image":"` + redisRef + `","network_mode":"host","command":["redis-server","--bind","127.0.0.1"]}}}`)
	inspect := []byte(`[{"Id":"` + fixtureImageID + `","Config":{"User":"0:0","WorkingDir":"/","Cmd":["600"],"Entrypoint":["/bin/sleep"],"Env":["BASE=fixture-sensitive-value"]}},{"Id":"` + serverID + `","Config":{"User":"0:0","WorkingDir":"/","Cmd":["redis-server"],"Entrypoint":["docker-entrypoint.sh"],"Env":["BASE=fixture-sensitive-value"]}}]`)
	body, _ := json.Marshal(imageDocument{Version: "jobseek.crawler-release-images/v1", FileEvidenceSHA256: strings.Repeat("a", 64), ComposeSHA256: digest(compose), Project: "jobseek", Architecture: "amd64", Services: map[string]serviceImage{"worker": {ref, fixtureImageID}, "redis": {redisRef, serverID}}})
	image := &Images{body: string(body), digest: digest(body), compose: compose, imageInspect: inspect}
	inventory := observeFixtureContainers(t, worker, server)
	execution, err := RequireContainerExecution(context.Background(), inventory, []*Images{image})
	if err != nil {
		t.Fatal("private selected Redis fixture", err)
	}
	return inventory, image, execution
}

func TestSelectedRedisEndpointJoinsEffectiveConsumersDaemonAndSocket(t *testing.T) {
	t.Setenv("REDIS_URL", "redis://caller-sensitive-value:1/0")
	inventory, image, execution := selectedRedisFixture(t)
	listener := func(ctx context.Context, pid int64, port int) (string, error) {
		if pid != 19 || port != 6379 {
			t.Fatal("socket join lost declared server identity")
		}
		return "1979", nil
	}
	e, err := requireSelectedRedisEndpoint(context.Background(), strings.Repeat("a", 64), inventory, []*Images{image}, execution, listener)
	if err != nil || e.connection != "redis://127.0.0.1:6379/0" || e.SHA256() != digest([]byte(e.Body())) {
		t.Fatal("selected Redis endpoint refused", err)
	}
	for _, private := range []string{"localhost", "127.0.0.1", "REDIS_URL", "BASE", "fixture-sensitive-value", "caller-sensitive-value"} {
		if strings.Contains(e.Body(), private) {
			t.Fatal("private selected execution disclosed")
		}
	}
	for _, fault := range []string{"wrong-selected", "wrong-execution", "missing-private-compose", "private-compose-drift", "consumer-missing-network", "consumer-wrong-endpoint", "server-not-observed", "socket-not-owned", "listener-missing", "unsupported-server-option"} {
		t.Run(fault, func(t *testing.T) {
			i, image, ex := selectedRedisFixture(t)
			sha := strings.Repeat("a", 64)
			observe := listener
			switch fault {
			case "wrong-selected":
				sha = strings.Repeat("b", 64)
			case "wrong-execution":
				ex = &Execution{body: ex.body, digest: strings.Repeat("b", 64)}
			case "missing-private-compose":
				image.compose = nil
			case "private-compose-drift":
				image.compose = []byte(strings.Replace(string(image.compose), "localhost", "other", 1))
			case "consumer-missing-network":
				private := i.commands[i.rows[0].ID]
				var host map[string]any
				_ = json.Unmarshal(private.host, &host)
				delete(host, "NetworkMode")
				private.host, _ = json.Marshal(host)
				i.commands[i.rows[0].ID] = private
				i.rows[0].HostConfigSHA256 = digest(private.host)
				// Rebind the fixture inventory/execution hashes so the endpoint
				// check, not incidental stale evidence, rejects the network gap.
				refreshRedisFixtureInventory(t, i)
				ex, _ = RequireContainerExecution(context.Background(), i, []*Images{image})
			case "consumer-wrong-endpoint":
				image.compose = []byte(strings.Replace(string(image.compose), "redis://localhost:6379/0", "redis://localhost:6379/1", 1))
			case "server-not-observed":
				i.rows = i.rows[:1]
				refreshRedisFixtureInventory(t, i)
				ex, _ = RequireContainerExecution(context.Background(), i, []*Images{image})
			case "socket-not-owned", "listener-missing":
				observe = func(context.Context, int64, int) (string, error) { return "", errors.New("fixture socket refused") }
			case "unsupported-server-option":
				image.compose = []byte(strings.Replace(string(image.compose), `"--bind","127.0.0.1"`, `"--bind","127.0.0.1","--daemonize","yes"`, 1))
			}
			if e, err := requireSelectedRedisEndpoint(context.Background(), sha, i, []*Images{image}, ex, observe); !errors.Is(err, ErrInvalid) || e != nil || strings.Contains(err.Error(), "sensitive") {
				t.Fatal("unverified selected Redis accepted or disclosed", err)
			}
		})
	}
}

func refreshRedisFixtureInventory(t *testing.T, c *Containers) {
	t.Helper()
	var doc map[string]json.RawMessage
	if json.Unmarshal([]byte(c.body), &doc) != nil {
		t.Fatal("fixture inventory framing")
	}
	// Locate the typed row array without hard-coding the schema's field label.
	for key, value := range doc {
		var rows []containerRow
		if json.Unmarshal(value, &rows) == nil && len(rows) > 0 && rows[0].ID != "" {
			doc[key], _ = json.Marshal(c.rows)
		}
	}
	b, _ := json.Marshal(doc)
	c.body, c.digest = string(b), digest(b)
}

func TestSelectedRedisConnectionAndServerContractIsClosed(t *testing.T) {
	for _, raw := range []string{"redis://remote:6379/0", "rediss://localhost:6379/0", "unix:///tmp/socket", "redis://localhost/0", "redis://localhost:06379/0", "redis://localhost:6379/00", "redis://localhost:6379/0?db=1", "redis://user:fixture-sensitive-value@localhost:6379/0", "redis://localhost:6379/0#fragment", "redis://localhost:6379/%30"} {
		if _, _, err := selectedRedisURL(raw); !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), "fixture-sensitive-value") {
			t.Fatal("caller-shaped Redis route accepted", err)
		}
	}
	for _, cmd := range [][]string{{"/bin/sh", "-c", "redis-server"}, {"redis-server", "/data/redis.conf"}, {"redis-server", "--bind", "0.0.0.0"}, {"redis-server", "--port", "6379"}, {"redis-server", "--bind", "127.0.0.1", "--port", "6379", "--port", "6379"}, {"redis-server", "--bind", "127.0.0.1", "--maxmemory-policy", "allkeys-lru"}} {
		if _, err := selectedRedisServerPort(cmd); err == nil {
			t.Fatal("unverified Redis command accepted")
		}
	}
}

func TestRedisSocketObservationRequiresUniqueLoopbackListenerOwnedByDaemon(t *testing.T) {
	for _, fault := range []string{"none", "absent", "wildcard", "duplicate", "wrong-owner", "malformed", "fd-drift", "ipv6-listener", "ipv6-malformed"} {
		t.Run(fault, func(t *testing.T) {
			root := t.TempDir()
			fd := filepath.Join(root, "19/fd")
			if os.MkdirAll(fd, 0700) != nil || os.MkdirAll(filepath.Join(root, "net"), 0700) != nil {
				t.Fatal("private proc fixture")
			}
			line := "  0: 0100007F:18EB 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 1979 1\n"
			inode := "1979"
			switch fault {
			case "absent":
				line = ""
			case "wildcard":
				line = strings.Replace(line, "0100007F", "00000000", 1)
			case "duplicate":
				line += line
			case "wrong-owner":
				inode = "1980"
			case "malformed":
				line = "broken\n"
			}
			if os.WriteFile(filepath.Join(root, "net/tcp"), []byte("sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"+line), 0600) != nil || os.Symlink("socket:["+inode+"]", filepath.Join(fd, "5")) != nil {
				t.Fatal("private listener fixture")
			}
			if fault == "fd-drift" && os.WriteFile(filepath.Join(fd, "6"), []byte("regular"), 0600) != nil {
				t.Fatal("private descriptor drift")
			}
			if fault == "ipv6-listener" || fault == "ipv6-malformed" {
				v6 := "  0: 00000000000000000000000000000000:18EB 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 1981 1\n"
				if fault == "ipv6-malformed" {
					v6 = "broken\n"
				}
				if os.WriteFile(filepath.Join(root, "net/tcp6"), []byte("sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"+v6), 0600) != nil {
					t.Fatal("private IPv6 listener fixture")
				}
			}
			got, err := redisListenerAt(context.Background(), root, 19, 6379)
			if fault == "none" {
				if err != nil || got != "1979" {
					t.Fatal("owned unique listener refused", err)
				}
			} else if err == nil {
				t.Fatal("ambiguous/unowned listener admitted")
			}
		})
	}
}
