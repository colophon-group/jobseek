//go:build integration

package releaseevidence

import (
	"context"
	"fmt"
	"net"
	"os"
	"runtime"
	"strconv"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// This owns only newly created disposable Actions fixtures. Never run it on a
// local/production Docker socket. Root is needed for Redis's /proc PID/fd view.
func TestActualSelectedRedisEndpointJoinsDeclaredConsumerAndDaemonSocket(t *testing.T) {
	if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_SELECTED_REDIS") != "1" {
		t.Skip("explicit disposable selected Redis fixture required")
	}
	if runtime.GOOS != "linux" || os.Getenv("GITHUB_ACTIONS") != "true" || os.Geteuid() != 0 {
		t.Fatal("selected Redis fixture requires trusted disposable Linux root")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()
	const redisRef = "redis:8-alpine@sha256:978f0e01593e65eed801f2402944efcd936d43b5027e4908a7897baf88ed6241"
	const workerRef = "postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
	probe, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal("private Redis fixture port")
	}
	port := probe.Addr().(*net.TCPAddr).Port
	_ = probe.Close()
	project := fmt.Sprintf("jobseek-selected-redis-%d", os.Getpid())
	url := "redis://localhost:" + strconv.Itoa(port) + "/0"
	commands := []string{"redis-server", "--bind", "127.0.0.1", "--port", strconv.Itoa(port), "--maxmemory", "16mb", "--maxmemory-policy", "noeviction", "--appendonly", "no", "--save", ""}
	ids := []string{}
	create := func(service string, args []string) string {
		t.Helper()
		base := []string{"run", "--detach", "--pull=never", "--network", "host", "--restart", "no", "--label", "com.docker.compose.project=" + project, "--label", "com.docker.compose.service=" + service, "--label", "com.docker.compose.oneoff=False"}
		out, err := readDocker(ctx, append(base, args...))
		id := strings.TrimSpace(string(out))
		if err != nil || !containerIDPattern.MatchString(id) {
			t.Fatal("owned Redis fixture container", err)
		}
		ids = append(ids, id)
		return id
	}
	t.Cleanup(func() {
		cleanup, done := context.WithTimeout(context.Background(), 20*time.Second)
		defer done()
		for _, id := range ids {
			if _, err := readDocker(cleanup, []string{"rm", "--force", id}); err != nil {
				t.Error("owned fixture cleanup", err)
			}
		}
	})
	server := create("redis", append([]string{redisRef}, commands...))
	_ = create("worker", []string{"--user", "0:0", "--env", "REDIS_URL=" + url, "--entrypoint", "/bin/sleep", workerRef, "90"})
	g := fixture(t)
	compose := "services:\n  redis:\n    image: " + redisRef + "\n    network_mode: host\n    command: ['redis-server', '--bind', '127.0.0.1', '--port', '" + strconv.Itoa(port) + "', '--maxmemory', '16mb', '--maxmemory-policy', 'noeviction', '--appendonly', 'no', '--save', '']\n  worker:\n    image: " + workerRef + "\n    user: '0:0'\n    network_mode: host\n    entrypoint: ['/bin/sleep']\n    command: ['90']\n    environment:\n      REDIS_URL: " + url + "\n"
	write(t, g, "docker-compose.yml", []byte(compose))
	refresh(t, g)
	files := verify(t, g)
	t.Setenv("REDIS_URL", "redis://caller-sensitive-value:1/0")
	t.Setenv("DOCKER_HOST", "tcp://caller-sensitive-value:1")
	observe := func() (*RedisEndpoint, error) {
		images, err := ObserveImages(ctx, ImageObservationConfig{Directory: g.directory, Owner: fixtureOwner, Project: project, FileEvidenceSHA256: files.SHA256(), Architecture: runtime.GOARCH})
		if err != nil {
			return nil, err
		}
		inventory, err := ObserveContainers(ctx)
		if err != nil {
			return nil, err
		}
		execution, err := RequireContainerExecution(ctx, inventory, []*Images{images})
		if err != nil {
			return nil, err
		}
		return RequireSelectedRedisEndpoint(ctx, files.SHA256(), inventory, []*Images{images}, execution)
	}
	var endpoint *RedisEndpoint
	deadline := time.Now().Add(15 * time.Second)
	for {
		endpoint, err = observe()
		if err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("actual selected endpoint observation", err)
		}
		time.Sleep(25 * time.Millisecond)
	}
	connection, err := endpoint.ConnectionURL(ctx)
	if err != nil {
		t.Fatal("private selected connection", err)
	}
	client, err := queue.Open(connection, queue.Settings{LeaseTTL: time.Minute, MaxDomains: 10})
	if err != nil {
		t.Fatal("native selected pool", err)
	}
	defer client.Close()
	instance, err := client.RedisInstanceSHA256(ctx)
	if err != nil || !shaPattern.MatchString(instance) {
		t.Fatal("actual selected Redis instance", err)
	}
	if again, err := observe(); err != nil || again.SHA256() != endpoint.SHA256() {
		t.Fatal("actual selected endpoint readback", err)
	}
	wrong := *endpoint
	wrong.pid = int64(os.Getpid())
	if wrong.Check(ctx) == nil {
		t.Fatal("another process adopted Redis listener")
	}
	for _, private := range []string{url, "caller-sensitive-value", "REDIS_URL", g.directory} {
		if strings.Contains(endpoint.Body(), private) {
			t.Fatal("private selected endpoint disclosed")
		}
	}
	if _, err := readDocker(ctx, []string{"stop", "--time", "3", server}); err != nil {
		t.Fatal("owned Redis stop", err)
	}
	if endpoint.Check(ctx) == nil {
		t.Fatal("stopped selected daemon retained socket authority")
	}
	if _, err := observe(); err == nil {
		t.Fatal("stopped selected daemon admitted")
	}
	if _, err := readDocker(ctx, []string{"start", server}); err != nil {
		t.Fatal("owned Redis restart", err)
	}
	var restarted *RedisEndpoint
	deadline = time.Now().Add(15 * time.Second)
	for {
		restarted, err = observe()
		if err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("owned Redis restart readback", err)
		}
		time.Sleep(25 * time.Millisecond)
	}
	if endpoint.Check(ctx) == nil || restarted.SHA256() == endpoint.SHA256() {
		t.Fatal("restarted daemon adopted prior socket observation")
	}
	connection, err = restarted.ConnectionURL(ctx)
	if err != nil {
		t.Fatal("restarted selected connection", err)
	}
	next, err := queue.Open(connection, queue.Settings{LeaseTTL: time.Minute, MaxDomains: 10})
	if err != nil {
		t.Fatal("restarted private pool", err)
	}
	defer next.Close()
	newInstance, err := next.RedisInstanceSHA256(ctx)
	if err != nil || newInstance == instance {
		t.Fatal("Redis restart incarnation not distinguished", err)
	}
	t.Log("actual selected Compose/image/container consumer endpoint joined to live official Redis host-network daemon and unique kernel listener inode owned by its PID; native read-only incarnation observed; caller endpoint/Docker environment ignored, wrong PID and stopped/restarted stale socket authority refused; sleeping consumer fixture only, producer/lease exclusion and complete host phase driver admission unproven")
}
