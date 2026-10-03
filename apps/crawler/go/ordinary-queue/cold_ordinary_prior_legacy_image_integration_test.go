//go:build integration

package queue

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"io/fs"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"strconv"
	"strings"
	"testing"
	"time"
)

// Last recorded production Python source. It has no joint ownership reader:
// host exclusion must keep it stopped until finalization closes both journals.
const priorLegacyImageSource = "b75ccb9456bf29c9477f9747c0c2cc3908ad79bb"

type priorLegacyImageEvidence struct {
	Version               string            `json:"version"`
	SourceRevision        string            `json:"source_revision"`
	ImageID               string            `json:"image_id"`
	Architecture          string            `json:"architecture"`
	PythonVersion         string            `json:"python_version"`
	PackageDirectory      string            `json:"package_directory"`
	EntrypointSHA256      string            `json:"entrypoint_sha256"`
	DockerfileSHA256      string            `json:"dockerfile_sha256"`
	InstalledSourceSHA256 map[string]string `json:"installed_source_sha256"`
	InstalledAssetSHA256  map[string]string `json:"installed_asset_sha256"`
}

func decodePriorLegacyImageEvidence(body []byte, digest, image, arch string) (priorLegacyImageEvidence, bool) {
	var e priorLegacyImageEvidence
	h := sha256.Sum256(body)
	if len(body) == 0 || len(body) > 512<<10 || !ownershipSHA256.MatchString(digest) || hex.EncodeToString(h[:]) != digest {
		return e, false
	}
	d := json.NewDecoder(bytes.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(&e) != nil || d.Decode(new(any)) != io.EOF {
		return e, false
	}
	canonical, err := json.Marshal(e)
	if err != nil || !bytes.Equal(canonical, body) || e.Version != "jobseek.migration.prior-legacy-installed/v1" || e.SourceRevision != priorLegacyImageSource || e.ImageID != image || !strings.HasPrefix(image, "sha256:") || !ownershipSHA256.MatchString(strings.TrimPrefix(image, "sha256:")) || e.Architecture != arch || (arch != "amd64" && arch != "arm64") || e.PythonVersion != "3.13.15" || e.PackageDirectory != "/app/.venv/lib/python3.13/site-packages/src" || !ownershipSHA256.MatchString(e.EntrypointSHA256) || !ownershipSHA256.MatchString(e.DockerfileSHA256) || len(e.InstalledSourceSHA256) == 0 || len(e.InstalledAssetSHA256) == 0 {
		return e, false
	}
	for _, m := range []map[string]string{e.InstalledSourceSHA256, e.InstalledAssetSHA256} {
		for name, hash := range m {
			if name == "." || !fs.ValidPath(name) || strings.ContainsAny(name, "\\\x00") || !ownershipSHA256.MatchString(hash) {
				return e, false
			}
		}
	}
	return e, true
}

func TestPriorLegacyImageEvidenceRejectsDrift(t *testing.T) {
	e := priorLegacyImageEvidence{"jobseek.migration.prior-legacy-installed/v1", priorLegacyImageSource, "sha256:" + strings.Repeat("a", 64), "amd64", "3.13.15", "/app/.venv/lib/python3.13/site-packages/src", strings.Repeat("b", 64), strings.Repeat("c", 64), map[string]string{"cli.py": strings.Repeat("d", 64)}, map[string]string{"images/epfl/icon.png": strings.Repeat("e", 64)}}
	accept := func(v priorLegacyImageEvidence) bool {
		b, _ := json.Marshal(v)
		returnOk := false
		_, returnOk = decodePriorLegacyImageEvidence(b, coldForwardBytesDigest(string(b)), e.ImageID, "amd64")
		return returnOk
	}
	if !accept(e) {
		t.Fatal("valid closed legacy image proof refused")
	}
	for _, mutate := range []func(*priorLegacyImageEvidence){
		func(v *priorLegacyImageEvidence) { v.SourceRevision = priorOrdinaryExecutableSource },
		func(v *priorLegacyImageEvidence) { v.ImageID = "jobseek-crawler:latest" },
		func(v *priorLegacyImageEvidence) { v.Architecture = "arm64" },
		func(v *priorLegacyImageEvidence) { v.PackageDirectory = "/app/src" },
		func(v *priorLegacyImageEvidence) {
			v.InstalledSourceSHA256 = map[string]string{"../cli.py": strings.Repeat("d", 64)}
		},
		func(v *priorLegacyImageEvidence) { v.InstalledAssetSHA256 = nil },
		func(v *priorLegacyImageEvidence) { v.EntrypointSHA256 = "" },
	} {
		v := e
		mutate(&v)
		if accept(v) {
			t.Fatal("legacy installed image drift admitted")
		}
	}
	b, _ := json.Marshal(e)
	for _, bad := range [][]byte{append(append([]byte{}, b...), '\n'), bytes.Replace(b, []byte(`"version":`), []byte(`"extra":true,"version":`), 1), bytes.Replace(b, []byte(`"version":`), []byte(`"source_revision":"bad","version":`), 1)} {
		if _, ok := decodePriorLegacyImageEvidence(bad, coldForwardBytesDigest(string(bad)), e.ImageID, "amd64"); ok {
			t.Fatal("ambiguous legacy proof admitted")
		}
	}
}

// Docker is owned solely by the disposable GitHub CI harness, never exposed to
// the agent or used against the production host. Run the actual installed old
// CLI in its immutable image, with only this fixture's PG and Redis authorities.
func TestPriorLegacyImageConsumesCompletedRestorationAtR(t *testing.T) {
	prefix := "JOBSEEK_ORDINARY_PRIOR_LEGACY_IMAGE_"
	image, path, digest := os.Getenv(prefix+"ID"), os.Getenv(prefix+"PROOF_FILE"), os.Getenv(prefix+"PROOF_SHA256")
	if image == "" && path == "" && digest == "" {
		if os.Getenv(prefix+"REQUIRE") == "1" {
			t.Fatal("required prior legacy image unavailable")
		}
		t.Skip("explicit disposable CI legacy image required")
	}
	if runtime.GOOS != "linux" || os.Getenv("GITHUB_ACTIONS") != "true" || !filepath.IsAbs(path) {
		t.Fatal("actual legacy container fixture requires isolated GitHub CI")
	}
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 512<<10 {
		t.Fatal("unsafe prior legacy image proof")
	}
	body, err := os.ReadFile(path)
	e, ok := decodePriorLegacyImageEvidence(body, digest, image, runtime.GOARCH)
	if err != nil || !ok {
		t.Fatal("legacy image proof identity refused")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	for base, m := range map[string]map[string]string{"src": e.InstalledSourceSHA256, "data": e.InstalledAssetSHA256} {
		// Assert the exact installed tree, including Lua and non-Python assets.
		listing, err := exec.CommandContext(ctx, "git", "ls-tree", "--full-tree", "-r", "--name-only", priorLegacyImageSource, "apps/crawler/"+base+"/").Output()
		if err != nil {
			t.Fatal("immutable prior tree unavailable")
		}
		if len(strings.Split(strings.TrimSpace(string(listing)), "\n")) != len(m) {
			t.Fatal("prior installed tree has missing or extra files", base)
		}
		for name, hash := range m {
			b, err := exec.CommandContext(ctx, "git", "show", priorLegacyImageSource+":apps/crawler/"+base+"/"+name).Output()
			if err != nil || coldForwardBytesDigest(string(b)) != hash {
				t.Fatal("prior installed file differs from immutable source", base, name)
			}
		}
	}
	dockerfile, err := exec.CommandContext(ctx, "git", "show", priorLegacyImageSource+":apps/crawler/Dockerfile").Output()
	if err != nil || coldForwardBytesDigest(string(dockerfile)) != e.DockerfileSHA256 {
		t.Fatal("prior Dockerfile drifted")
	}
	identity, err := exec.CommandContext(ctx, "docker", "image", "inspect", "--format", "{{.Id}} {{.Architecture}}", image).Output()
	if err != nil || strings.TrimSpace(string(identity)) != image+" "+runtime.GOARCH {
		t.Fatal("actual legacy image identity drifted")
	}
	t.Logf("verified prior legacy source=%s image_id=%s architecture=%s installed_source_files=%d installed_assets=%d", e.SourceRevision, image, e.Architecture, len(e.InstalledSourceSHA256), len(e.InstalledAssetSHA256))
	p, plan, control := finalizationFixtureWithPriorSource(t, false, false, priorLegacyImageSource)
	if frozen, err := p.f.client.redis.Persist(ctx, "ratelimit:jobs.example.test").Result(); err != nil || !frozen {
		t.Fatal("private B0 baseline not frozen")
	}
	if _, err := publishColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	saved := forwardRedisSnapshot(t, p.f.client)
	restartPublicationRedisWithoutSave(t, p.f.client)
	assertFinalizationPriorSnapshot(t, saved, forwardRedisSnapshot(t, p.f.client))
	if _, err := completeColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	if publicationPhase(t, p) != "reversed" || finalizationUnselected(ctx, p) != nil || p.f.client.redis.Exists(ctx, ownershipProjectionKey).Val() != 0 {
		t.Fatal("legacy host containment must wait for complete restoration")
	}
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", p.f.task.ID); err != nil {
		t.Fatal(err)
	}
	var due time.Time
	var failures int
	if err := p.f.observer.QueryRow(ctx, "SELECT next_check_at,consecutive_failures FROM job_board WHERE id=$1::uuid", p.f.task.ID).Scan(&due, &failures); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	_ = listener.Close()
	container := "jobseek-prior-legacy-" + ordinaryID(t)
	log, err := os.OpenFile(filepath.Join(t.TempDir(), "prior-runtime.log"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		t.Fatal(err)
	}
	defer log.Close()
	// Historical image defaults to root. DAC_OVERRIDE lets that actual user
	// reach the fixture-owned 0700 Redis socket; no socket permissions change.
	args := []string{"run", "--rm", "--name", container, "--network", "host", "--read-only", "--cap-drop", "ALL", "--cap-add", "DAC_OVERRIDE", "--security-opt", "no-new-privileges", "--pids-limit", "256", "--memory", "1g", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--mount", "type=bind,source=" + filepath.Dir(p.f.client.redis.Options().Addr) + ",target=/fixture-redis,readonly", "--entrypoint", "/app/.venv/bin/crawler"}
	for _, v := range []string{"LOCAL_DATABASE_URL=" + p.f.dsn, "REDIS_URL=unix:///fixture-redis/redis.sock", "METRICS_PORT=" + strconv.Itoa(port), "DISCOVERY_CONCURRENCY=1", "MONITOR_CONCURRENCY=1", "SHUTDOWN_GRACE_SECONDS=1", "CRAWLER_DB_POOL_MIN=0", "CRAWLER_DB_POOL_MAX=1", "LIGHTPANDA_B0_PRODUCER_MODE=off", "PYTHONDONTWRITEBYTECODE=1"} {
		args = append(args, "--env", v)
	}
	args = append(args, image, "run")
	cmd := exec.CommandContext(ctx, "docker", args...)
	cmd.Stdout, cmd.Stderr = log, log
	started := time.Now()
	if err := cmd.Start(); err != nil {
		t.Fatal("actual prior legacy image failed to start")
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	waited := false
	t.Cleanup(func() {
		if !waited {
			cleanup, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			_ = exec.CommandContext(cleanup, "docker", "rm", "--force", container).Run()
			_ = cmd.Process.Kill()
			<-done
		}
	})
	client := &http.Client{Timeout: time.Second}
	defer client.CloseIdleConnections()
	settled := false
	for deadline := time.Now().Add(30 * time.Second); time.Now().Before(deadline); {
		select {
		case <-done:
			waited = true
			t.Fatal("prior legacy image exited before policy settlement; private runtime log retained locally")
		default:
		}
		response, err := client.Get("http://127.0.0.1:" + strconv.Itoa(port) + "/metrics")
		if err == nil {
			b, readErr := io.ReadAll(io.LimitReader(response.Body, 2<<20))
			_ = response.Body.Close()
			noFetch := strings.Contains(string(b), `crawler_runtime_origin_attempts_total{egress="direct",execution_class="http",stage="monitor"} 0.0`)
			for _, line := range strings.Split(string(b), "\n") {
				if strings.HasPrefix(line, "crawler_runtime_origin_attempts_total{") && !strings.HasSuffix(line, " 0.0") {
					noFetch = false
				}
			}
			settled = readErr == nil && response.StatusCode == 200 && noFetch && strings.Contains(string(b), `crawler_tasks_total{kind="monitor",status="tdm_reserved"} 1.0`) && p.f.client.redis.ZCard(ctx, "inflight:simple").Val() == 0
			if settled {
				break
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	if !settled {
		t.Fatal("prior legacy image did not settle its historical no-fetch policy cycle")
	}
	if err := exec.CommandContext(ctx, "docker", "kill", "--signal", "SIGTERM", container).Run(); err != nil {
		t.Fatal("prior legacy SIGTERM failed")
	}
	select {
	case err := <-done:
		waited = true
		if err != nil {
			t.Fatal("prior legacy image failed graceful drain")
		}
	case <-time.After(10 * time.Second):
		t.Fatal("prior legacy image did not drain")
	}
	var afterDue time.Time
	var afterFailures int
	if err := p.f.observer.QueryRow(ctx, "SELECT next_check_at,consecutive_failures FROM job_board WHERE id=$1::uuid", p.f.task.ID).Scan(&afterDue, &afterFailures); err != nil || !due.Equal(afterDue) || failures != afterFailures {
		t.Fatal("legacy reserved cycle changed canonical due or failure budget")
	}
	score, err := p.f.client.redis.ZScore(ctx, "monitors_simple:"+p.f.task.Domain, p.f.task.ID).Result()
	// Preserve the OLD worker's own interval rule, explicitly different from
	// the native worker's canonical SQL/Redis due equality. This is not a new
	// parity claim for ordinary fetch cycles or every historical profile.
	if err != nil || score < float64(started.UnixMilli())/1e3+3600 || score > float64(time.Now().UnixMilli())/1e3+3600 || p.f.client.redis.ZCard(ctx, "monitors_simple:"+p.f.task.Domain).Val() != 1 || p.f.client.redis.ZCard(ctx, "deadletter:simple").Val() != 0 || p.f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("legacy reserved cycle lost queue ACK/interval conservation")
	}
	var epoch int64
	var active int
	if err := p.f.observer.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epoch); err != nil || epoch != plan.RetirementEpoch() {
		t.Fatal("legacy worker advanced R")
	}
	if err := p.f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_ownership_plan WHERE state='active'").Scan(&active); err != nil || active != 0 {
		t.Fatal("legacy worker invented native ownership")
	}
	after := forwardRedisSnapshot(t, p.f.client)
	for key, value := range saved {
		if strings.HasPrefix(key, "lightpanda-b0:") && !reflect.DeepEqual(value, after[key]) {
			t.Fatal("legacy monitor disturbed restored B0 authority", key)
		}
	}
	assertCanonical(t, p.f, false)
	t.Log("actual prior legacy image completed reserved monitor, queue ACK, unchanged canonical content/due/failure budget, historical queue interval and SIGTERM drain at R")
}
