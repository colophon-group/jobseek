//go:build integration

package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

// Run only in the disposable installed-image Actions harness. This owns its
// containers, uses sleeping consumers, and proves the joined native library
// path. Complete installed cold CLI/graph and producer exclusion remain open.
func TestActualHostSelectedRedisJournalJoinsScopeAndExactReservation(t *testing.T) {
	if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_HOST_SELECTED_REDIS") != "1" {
		t.Skip("explicit disposable joined fixture required")
	}
	if runtime.GOOS != "linux" || os.Getenv("GITHUB_ACTIONS") != "true" || os.Geteuid() != 0 || os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY") == "" {
		t.Fatal("joined fixture requires disposable Linux root and installed image")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 900*time.Second)
	defer cancel()
	source := ordinaryFixtureSourceRevision(t)
	const project = "jobseek-native-host-contain" // Owned Actions PostgreSQL labels.
	const database = "postgresql://crawler:crawler@127.0.0.1:5432/jobseek_ordinary_worker_test?sslmode=disable"
	const redisRef = "redis:8-alpine@sha256:978f0e01593e65eed801f2402944efcd936d43b5027e4908a7897baf88ed6241"
	b, err := os.ReadFile(os.Getenv("JOBSEEK_CRAWLER_RELEASE_HOST_JOIN_PROOF"))
	var proof struct {
		Source       string            `json:"source_revision"`
		Image        string            `json:"image_id"`
		Architecture string            `json:"architecture"`
		Binary       string            `json:"binary_sha256"`
		CA           string            `json:"system_ca_sha256"`
		Assets       map[string]string `json:"installed_asset_sha256"`
		Reference    string            `json:"immutable_reference"`
	}
	if err != nil || len(b) > 1<<20 || json.Unmarshal(b, &proof) != nil || proof.Source != source || proof.Architecture != runtime.GOARCH || len(proof.Assets) != 34 || !strings.HasPrefix(proof.Reference, "localhost:15000/native@sha256:") || !planPattern.MatchString(strings.TrimPrefix(proof.Reference, "localhost:15000/native@sha256:")) || !strings.HasPrefix(proof.Image, "sha256:") || !planPattern.MatchString(strings.TrimPrefix(proof.Image, "sha256:")) || !planPattern.MatchString(proof.Binary) || !planPattern.MatchString(proof.CA) {
		t.Fatal("independent joined installed identity")
	}
	expect := release.InstalledExpectationSpec{Version: "jobseek.crawler-installed-expectation/v1", SourceRevision: source, ImageID: proof.Image, Architecture: proof.Architecture, Kind: "native-ordinary", BinarySHA256: proof.Binary, CASHA256: proof.CA, Assets: proof.Assets}
	probe, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal("owned Redis fixture port")
	}
	port := probe.Addr().(*net.TCPAddr).Port
	_ = probe.Close()
	selectedURL := "redis://localhost:" + strconv.Itoa(port) + "/0"
	dockerEnv := []string{"PATH=/usr/bin:/bin", "HOME=/", "LC_ALL=C", "LANG=C", "DOCKER_CONFIG=/run/jobseek-native-host-fixture-docker-config", "DOCKER_HOST=unix:///var/run/docker.sock"}
	docker := func(args ...string) ([]byte, error) {
		callCtx, done := context.WithTimeout(ctx, 20*time.Second)
		defer done()
		cmd := exec.CommandContext(callCtx, "/usr/bin/docker", args...)
		cmd.Env = dockerEnv
		return cmd.Output()
	}
	ids := []string{}
	t.Cleanup(func() {
		cleanup, done := context.WithTimeout(context.Background(), 30*time.Second)
		defer done()
		for _, id := range ids {
			cmd := exec.CommandContext(cleanup, "/usr/bin/docker", "rm", "--force", id)
			cmd.Env = dockerEnv
			if cmd.Run() != nil {
				t.Error("owned joined fixture cleanup")
			}
		}
	})
	create := func(service string, args []string) string {
		t.Helper()
		base := []string{"run", "--detach", "--pull=never", "--network", "host", "--restart", "no", "--label", "com.docker.compose.project=" + project, "--label", "com.docker.compose.service=" + service, "--label", "com.docker.compose.oneoff=False"}
		out, err := docker(append(base, args...)...)
		id := strings.TrimSpace(string(out))
		if err != nil || !hostContainerPattern.MatchString(id) {
			t.Fatal("owned joined fixture container", err)
		}
		ids = append(ids, id)
		return id
	}
	server := create("redis", []string{redisRef, "redis-server", "--bind", "127.0.0.1", "--port", strconv.Itoa(port), "--maxmemory", "32mb", "--maxmemory-policy", "noeviction", "--appendonly", "no", "--save", ""})
	consumerIDs := map[string]string{}
	for _, service := range []string{"worker", "exporter"} {
		consumerIDs[service] = create(service, []string{"--user", "10001:10001", "--env", "REDIS_URL=" + selectedURL, "--entrypoint", "/bin/sleep", proof.Reference, "600"})
	}
	options, err := redis.ParseURL(selectedURL)
	if err != nil {
		t.Fatal("private fixture URL")
	}
	selected := redis.NewClient(options)
	t.Cleanup(func() { _ = selected.Close() })
	deadline := time.Now().Add(10 * time.Second)
	for selected.Ping(ctx).Err() != nil {
		if time.Now().After(deadline) {
			t.Fatal("owned Redis readiness")
		}
		time.Sleep(10 * time.Millisecond)
	}
	// Reuse only private data setup. Its fixture host scope/store are discarded;
	// actual authority below comes from the production host and endpoint APIs.
	f, _, _, previous, prepared, _ := hostPhaseTestFixture(t)
	keys, err := f.r.Keys(ctx, "*").Result()
	if err != nil || len(keys) > 256 {
		t.Fatal("bounded private fixture seed")
	}
	for _, key := range keys {
		value, err := f.r.Dump(ctx, key).Result()
		if err != nil || selected.Restore(ctx, key, 0, value).Err() != nil {
			t.Fatal("owned fixture seed copy")
		}
	}
	client, err := queue.Open(selectedURL, queue.Settings{LeaseTTL: time.Minute, MaxDomains: 10})
	if err != nil {
		t.Fatal("private seed observer")
	}
	t.Cleanup(func() { _ = client.Close() })
	f.r, f.client = selected, client
	selectedInstance, err := client.RedisInstanceSHA256(ctx)
	if err != nil {
		t.Fatal("owned selected server incarnation")
	}
	clientCount := func() int {
		t.Helper()
		body, err := selected.ClientList(ctx).Result()
		if err != nil || len(body) > 64<<10 {
			t.Fatal("bounded owned server client census")
		}
		return len(strings.Split(strings.TrimSpace(body), "\n"))
	}
	baselineClients := clientCount()
	canonicalBefore, redisBefore := coldExecutableCanonicalSnapshot(t, f), fullColdExecutableRedisSnapshot(t, f)
	deployment := hostPrivateDirectory(t)
	if os.WriteFile(filepath.Join(deployment, "docker-compose.yml"), []byte("services: {}\n"), 0600) != nil {
		t.Fatal("joined private deployment")
	}
	r := HostPreflightRequest{Version: "jobseek.crawler-host-preflight-request/v1", CoordinatorSource: source, Owner: "colophon-group", Project: project, Architecture: runtime.GOARCH, DeploymentDirectory: deployment}
	compose := "services:\n  postgres:\n    image: postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193\n    environment:\n      POSTGRES_USER: crawler\n      POSTGRES_PASSWORD: crawler\n      POSTGRES_DB: jobseek_ordinary_worker_test\n      GITHUB_ACTIONS: 'true'\n      CI: 'true'\n  redis:\n    image: " + redisRef + "\n    network_mode: host\n    command: ['redis-server','--bind','127.0.0.1','--port','" + strconv.Itoa(port) + "','--maxmemory','32mb','--maxmemory-policy','noeviction','--appendonly','no','--save','']\n"
	for _, service := range []string{"worker", "exporter"} {
		compose += "  " + service + ":\n    image: " + proof.Reference + "\n    user: '10001:10001'\n    network_mode: host\n    entrypoint: ['/bin/sleep']\n    command: ['600']\n    environment:\n      REDIS_URL: " + selectedURL + "\n"
		r.Installed = append(r.Installed, HostInstalledRequest{"active", service, consumerIDs[service], expect})
	}
	for _, role := range []string{"active", "incoming", "rollback"} {
		g, files := releaseExecutableGeneration(t, source)
		if role == "active" {
			g = hostSelectActiveFixture(t, deployment, g)
		}
		newEnv := files["environment.env"] + "LOCAL_DATABASE_URL=" + database + "\n"
		manifest := strings.ReplaceAll(files["release.manifest"], hostDigest([]byte(files["docker-compose.yml"])), hostDigest([]byte(compose)))
		manifest = strings.ReplaceAll(manifest, hostDigest([]byte(files["environment.env"])), hostDigest([]byte(newEnv)))
		for name, body := range map[string]string{"docker-compose.yml": compose, "docker-compose.sha256": hostDigest([]byte(compose)) + "\n", "environment.env": newEnv, "environment.sha256": hostDigest([]byte(newEnv)) + "\n", "release.manifest": manifest} {
			if os.WriteFile(filepath.Join(g, name), []byte(body), 0600) != nil {
				t.Fatal("joined generation fixture")
			}
		}
		evidence, err := release.VerifyFiles(ctx, g, r.Owner)
		if err != nil {
			t.Fatal("joined generation verification", err)
		}
		r.Releases = append(r.Releases, HostReleaseRequest{role, g, evidence.SHA256()})
	}
	state := hostPrivateDirectory(t)
	body, _ := json.Marshal(r)
	if os.WriteFile(filepath.Join(state, "request.json"), body, 0600) != nil {
		t.Fatal("joined protected request")
	}
	cmd := exec.CommandContext(ctx, os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY"), "--host-preflight")
	cmd.Env = []string{"ORDINARY_GO_WORKER_MODE=host-preflight", "ORDINARY_HOST_COORDINATOR_SOURCE_REVISION=" + source, "ORDINARY_HOST_REQUEST_DIRECTORY=" + state, "ORDINARY_HOST_REQUEST_SHA256=" + hostDigest(body), "REDIS_URL=invalid-caller-value", "DOCKER_HOST=tcp://127.0.0.1:1"}
	out, err := cmd.CombinedOutput()
	var preflight HostPreflightResult
	if err != nil || json.Unmarshal(out, &preflight) != nil || preflight.RuntimeAdmission {
		hostContainmentFixtureDiagnostic(t, ctx, r)
		t.Fatal("joined installed preflight", err)
	}
	if ClearHostDatabaseEnvironment() != nil {
		t.Fatal("explicit root coordinator environment")
	}
	t.Setenv("REDIS_URL", "redis://caller-sensitive-value:1/0")
	t.Setenv("DOCKER_HOST", "tcp://caller-sensitive-value:1")
	env := map[string]string{"ORDINARY_GO_WORKER_MODE": "host-quiesce", "ORDINARY_HOST_COORDINATOR_SOURCE_REVISION": source, "ORDINARY_HOST_REQUEST_DIRECTORY": state, "ORDINARY_HOST_REQUEST_SHA256": hostDigest(body), "ORDINARY_HOST_PREFLIGHT_INTENT_SHA256": preflight.IntentSHA256}
	config, err := ReadHostQuiescenceConfig(func(key string) string { return env[key] }, source)
	if err != nil {
		t.Fatal("joined explicit configuration")
	}
	lua, err := os.ReadFile("../../src/lua/lightpanda_b0_queue.lua")
	if err != nil {
		t.Fatal("retained reviewed fixture Lua")
	}
	luaSHA := hostPhaseRetainTestInput(t, state, lua)
	var originalInfo HostColdPhaseContext
	var escaped context.Context
	var escapedPool *pgxpool.Pool
	var requestSHAs []string
	var outcomes []*HostColdPhaseResult
	var sqlPIDs []int32
	assertHeld := func(scoped context.Context, pool *pgxpool.Pool, sql *queue.HostColdSQL) {
		t.Helper()
		if queue.CheckHostColdSQLBinding(scoped, pool, originalInfo.Binding) != nil {
			t.Fatal("joined phase lost selected SQL scope")
		}
		var observed struct {
			PID  int32   `json:"backend_pid"`
			Keys []int64 `json:"exclusive_barriers"`
		}
		if json.Unmarshal([]byte(sql.Body()), &observed) != nil || observed.PID <= 0 || len(observed.Keys) != 3 {
			t.Fatal("joined live SQL observation")
		}
		for _, key := range observed.Keys {
			var entered bool
			if f.pg.QueryRow(scoped, "SELECT pg_try_advisory_xact_lock_shared($1)", key).Scan(&entered) != nil || entered {
				t.Fatal("shared writer entered joined phase")
			}
		}
		lock, err := os.OpenFile(hostMutationLock, os.O_RDWR, 0)
		if err != nil {
			t.Fatal("joined host lock observation")
		}
		defer lock.Close()
		if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != syscall.EWOULDBLOCK {
			if err == nil {
				_ = syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
			}
			t.Fatal("joined phase lost original host flock")
		}
	}
	for retry := 0; retry < 2; retry++ {
		result, err := WithHostQuiescence(ctx, config, func(scoped context.Context, pool *pgxpool.Pool, sql *queue.HostColdSQL) error {
			var observed struct {
				PID int32 `json:"backend_pid"`
			}
			if json.Unmarshal([]byte(sql.Body()), &observed) != nil {
				t.Fatal("joined SQL PID")
			}
			sqlPIDs = append(sqlPIDs, observed.PID)
			return WithSelectedHostColdRedis(scoped, pool, func(selectedCtx context.Context) error {
				info, err := InspectHostColdPhaseContext(selectedCtx, pool)
				if err != nil || info.RuntimeAdmission || !planPattern.MatchString(info.RedisEndpointSHA256) || !planPattern.MatchString(info.RedisInstanceSHA256) || info.RedisInstanceSHA256 != selectedInstance || info.Binding.RequestSHA256 != hostDigest(body) || !planPattern.MatchString(info.ColdAttestationSHA256) || info.Binding.SourceRevision != source || info.ActiveReleaseSHA256 != r.Releases[0].FileEvidenceSHA256 || info.TargetReleaseSHA256 != r.Releases[1].FileEvidenceSHA256 || info.RollbackReleaseSHA256 != r.Releases[2].FileEvidenceSHA256 {
					t.Fatal("actual selected phase binding", err)
				}
				if retry == 0 {
					originalInfo = info
				} else if originalInfo != info {
					t.Fatal("new SQL backend changed selected Redis/cold bindings")
				}
				escaped, escapedPool = selectedCtx, pool
				if clientCount() <= baselineClients {
					t.Fatal("selected scope did not open its own client")
				}
				assertHeld(selectedCtx, pool, sql)
				call := func(req HostColdPhaseRequest) *HostColdPhaseResult {
					t.Helper()
					req.Version = "jobseek.crawler-host-cold-request/v2"
					req.Binding = info.Binding
					req.RedisEndpointSHA256 = info.RedisEndpointSHA256
					req.RedisInstanceSHA256 = info.RedisInstanceSHA256
					req.PreviousEpoch = previous
					sha := hostPhaseRetainTestRequest(t, state, req)
					result, err := RunHostColdPhase(selectedCtx, pool, sha)
					if err != nil || result == nil || result.Outcome != "completed" || result.RuntimeAdmission {
						t.Fatal("joined retained phase", req.Operation, err)
					}
					assertHeld(selectedCtx, pool, sql)
					requestSHAs = append(requestSHAs, sha)
					outcomes = append(outcomes, result)
					return result
				}
				if retry == 0 {
					target := call(HostColdPhaseRequest{Operation: "cold-b0-target", LuaSHA256: luaSHA, Namespace: "host-selected-redis", Shard: "lightpanda-b0", Cohort: "c1"})
					intent := hostPhaseRetainTestInput(t, state, hostPhaseTestIntent(t, info, previous, prepared, target))
					begun := call(HostColdPhaseRequest{Operation: "cold-begin", PredecessorSHA256: hostPhaseResultSHA(t, target), IntentSHA256: intent, TargetSHA256: target.Native.B0TargetSHA256, LuaSHA256: luaSHA})
					reserved := call(HostColdPhaseRequest{Operation: "cold-reserve", PredecessorSHA256: hostPhaseResultSHA(t, begun), IntentSHA256: intent})
					inspected := call(HostColdPhaseRequest{Operation: "cold-inspect", PredecessorSHA256: hostPhaseResultSHA(t, reserved), IntentSHA256: intent})
					if inspected.Native.RoutingEpoch != reserved.Native.RoutingEpoch || inspected.Native.PlanSHA256 != reserved.Native.PlanSHA256 {
						t.Fatal("joined inspection changed reservation")
					}
				} else {
					for n, sha := range requestSHAs {
						result, err := RunHostColdPhase(selectedCtx, pool, sha)
						if err != nil || !reflect.DeepEqual(result, outcomes[n]) {
							t.Fatal("joined exact retry changed historical result", err)
						}
						assertHeld(selectedCtx, pool, sql)
					}
				}
				without := context.WithValue(selectedCtx, hostColdRedisKey{}, nil)
				if result, err := RunHostColdPhase(without, pool, requestSHAs[0]); err == nil || result != nil {
					t.Fatal("host SQL alone granted Redis phase")
				}
				return nil
			})
		})
		if err != nil || result == nil || !result.SQLBarriersObserved || result.RuntimeAdmission {
			t.Fatal("joined callback completion", err)
		}
		if clientCount() != baselineClients {
			t.Fatal("completed selected scope retained Redis connections")
		}
		if _, err := InspectHostColdPhaseContext(escaped, escapedPool); err == nil {
			t.Fatal("escaped selected phase context admitted")
		}
		if result, err := RunHostColdPhase(escaped, escapedPool, requestSHAs[0]); err == nil || result != nil {
			t.Fatal("escaped selected context ran historical phase")
		}
		var locks int
		if f.pg.QueryRow(ctx, "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND pid=$1", sqlPIDs[retry]).Scan(&locks) != nil || locks != 0 {
			t.Fatal("joined callback retained SQL barriers")
		}
	}
	if len(sqlPIDs) != 2 || sqlPIDs[0] <= 0 || sqlPIDs[0] == sqlPIDs[1] {
		t.Fatal("exact retry did not use a new private SQL backend")
	}
	var lastEpoch int64
	if f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&lastEpoch) != nil || lastEpoch != outcomes[2].Native.RoutingEpoch {
		t.Fatal("joined retry reserved another epoch")
	}
	if !reflect.DeepEqual(canonicalBefore, coldExecutableCanonicalSnapshot(t, f)) || !reflect.DeepEqual(redisBefore, fullColdExecutableRedisSnapshot(t, f)) {
		t.Fatal("joined cold phase changed canonical rows or complete Redis values")
	}
	retained := map[string]string{}
	entries, err := os.ReadDir(state)
	if err != nil {
		t.Fatal("joined protected receipts")
	}
	for _, entry := range entries {
		if !strings.HasPrefix(entry.Name(), "cold-redis-") || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		body, err := os.ReadFile(filepath.Join(state, entry.Name()))
		if err != nil || bytes.Contains(body, []byte(selectedURL)) || bytes.Contains(body, []byte("caller-sensitive-value")) {
			t.Fatal("joined Redis receipt disclosed private inputs")
		}
		retained[entry.Name()] = hostDigest(body)
	}
	if len(retained) != 1 {
		t.Fatal("selected connection exact retry changed protected receipt")
	}
	if out, err := docker("inspect", "--format", "{{.State.Running}}", server); err != nil || strings.TrimSpace(string(out)) != "true" {
		t.Fatal("selected Redis daemon lost after phase")
	}
	phaseRecords := []map[string]string{}
	for n, sha := range requestSHAs {
		request, err := os.ReadFile(filepath.Join(state, "cold-request-"+sha+".json"))
		if err != nil || hostDigest(request) != sha {
			t.Fatal("joined exact retained request")
		}
		result, err := os.ReadFile(filepath.Join(state, "cold-result-"+sha+".json"))
		if err != nil || hostDigest(result) != hostPhaseResultSHA(t, outcomes[n]) {
			t.Fatal("joined exact retained result")
		}
		completed, err := os.ReadFile(filepath.Join(state, "cold-completed-"+sha+".json"))
		if err != nil {
			t.Fatal("joined retained completion")
		}
		phaseRecords = append(phaseRecords, map[string]string{"request_sha256": sha, "request_json": string(request), "result_sha256": hostDigest(result), "result_json": string(result), "completion_json": string(completed)})
	}
	connectionReceipts := map[string]string{}
	for name := range retained {
		body, err := os.ReadFile(filepath.Join(state, name))
		if err != nil {
			t.Fatal("joined retained connection")
		}
		connectionReceipts[name] = string(body)
	}
	phaseInputs := map[string]string{}
	for _, sha := range []string{luaSHA, outcomes[0].Native.B0TargetSHA256, outcomes[1].Native.IntentSHA256} {
		if !planPattern.MatchString(sha) {
			t.Fatal("joined bounded retained input identity")
		}
		body, err := os.ReadFile(filepath.Join(state, "cold-input-"+sha))
		if err != nil || hostDigest(body) != sha {
			t.Fatal("joined exact retained input")
		}
		phaseInputs[sha] = string(body)
	}
	joinedProof := map[string]any{"version": "jobseek.fixture.host-selected-redis-journal/v1", "source_revision": source, "architecture": runtime.GOARCH, "installed_image_id": proof.Image, "installed_binary_sha256": proof.Binary, "selected_redis_endpoint_sha256": originalInfo.RedisEndpointSHA256, "redis_instance_sha256": originalInfo.RedisInstanceSHA256, "cold_attestation_sha256": originalInfo.ColdAttestationSHA256, "phase_request_sha256": requestSHAs, "reservation_epoch": lastEpoch, "sql_backend_pids": sqlPIDs, "connection_receipts_sha256": retained, "phase_context": originalInfo, "host_request_sha256": hostDigest(body), "phase_inputs": phaseInputs, "phase_records": phaseRecords, "connection_receipts": connectionReceipts, "released_redis_client_count": baselineClients, "canonical_and_complete_redis_values_conserved": true, "runtime_admission": false, "scope": "actual native library callback with installed image preflight, sleeping consumer stand-ins and private SQL/Redis; complete installed cold CLI/phase graph/producer exclusion/runtime admission unproven"}
	encoded, err := json.MarshalIndent(joinedProof, "", "  ")
	proofPath := os.Getenv("JOBSEEK_CRAWLER_RELEASE_HOST_SELECTED_REDIS_PROOF")
	if err != nil || proofPath == "" || os.WriteFile(proofPath, append(encoded, '\n'), 0600) != nil {
		t.Fatal("joined fixture proof retention")
	}
	t.Log("actual host callback joined selected Compose/image/consumer execution and official Redis PID/listener inode to native target/intent/reservation/inspection journal effects; original mutation flock and all SQL barriers held across independent commits; new backend exact retry kept endpoint/incarnation, epoch and results, canonical and complete Redis values conserved, missing/escaped scope refused and resources released; installed preflight plus native library/sleeping consumer fixture only, complete cold CLI/phase graph/producer exclusion/runtime admission unproven")
}
