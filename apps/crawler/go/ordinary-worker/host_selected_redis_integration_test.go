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
	runActualHostSelectedRedisJournal(t, false, false)
}

func TestActualHostSelectedRedisForwardJournalUsesInstalledProducer(t *testing.T) {
	runActualHostSelectedRedisJournal(t, true, false)
}

func TestActualHostSelectedRedisRestorationJournalUsesInstalledProducer(t *testing.T) {
	runActualHostSelectedRedisJournal(t, true, true)
}

func runActualHostSelectedRedisJournal(t *testing.T, forward, restore bool) {
	if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_HOST_SELECTED_REDIS") != "1" {
		t.Skip("explicit disposable joined fixture required")
	}
	if runtime.GOOS != "linux" || os.Getenv("GITHUB_ACTIONS") != "true" || os.Geteuid() != 0 || os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY") == "" {
		t.Fatal("joined fixture requires disposable Linux root and installed image")
	}
	limit := 900 * time.Second
	if forward {
		limit = 1800 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), limit)
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
	var forwardSeed *hostForwardFixtureSeed
	if forward {
		forwardSeed = seedHostForwardFixture(t, ctx, f)
	}
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
	originalBaselineClients := baselineClients
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
	var producerProof map[string]any
	var stopProducer func()
	var forwardAfter map[string]string
	var forwardPreview map[string]string
	var restoration *hostForwardRestorationProof
	var forwardBeforeBootstrap map[string]string
	interrupted := []string{}
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
	var lastEpoch int64
	if err := WithHostMutationScope(ctx, func(mutationCtx context.Context) error {
		for retry := 0; retry < 2; retry++ {
			result, err := WithHostQuiescence(mutationCtx, config, func(scoped context.Context, pool *pgxpool.Pool, sql *queue.HostColdSQL) error {
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
						started := time.Now()
						deadline, ok := selectedCtx.Deadline()
						if !ok || time.Until(deadline) <= 0 {
							t.Fatal("joined cold graph has no live bounded deadline")
						}
						t.Logf("joined cold stage begin operation=%s remaining_ms=%d", req.Operation, time.Until(deadline).Milliseconds())
						req.Version = "jobseek.crawler-host-cold-request/v2"
						req.Binding = info.Binding
						req.RedisEndpointSHA256 = info.RedisEndpointSHA256
						req.RedisInstanceSHA256 = info.RedisInstanceSHA256
						req.PreviousEpoch = previous
						sha := hostPhaseRetainTestRequest(t, state, req)
						if forward && (req.Operation == "cold-b0-forward-apply" || req.Operation == "cold-forward-publish" || restore && (req.Operation == "cold-reversal-reserve" || req.Operation == "cold-b0-rollback-restore")) {
							result, err := runHostColdPhase(selectedCtx, pool, sha, func(stage string) error {
								if stage == "native_effect_returned" {
									return errHostPreflight
								}
								return nil
							})
							if err == nil || result != nil {
								t.Fatal("forward retained-effect seam did not interrupt")
							}
							assertHeld(selectedCtx, pool, sql)
							interrupted = append(interrupted, req.Operation)
						}
						result, err := RunHostColdPhase(selectedCtx, pool, sha)
						t.Logf("joined cold stage returned operation=%s elapsed_ms=%d remaining_ms=%d context_error=%v", req.Operation, time.Since(started).Milliseconds(), time.Until(deadline).Milliseconds(), selectedCtx.Err())
						if err != nil || result == nil || result.Outcome != "completed" || result.RuntimeAdmission {
							t.Fatal("joined retained phase", req.Operation, err, result)
						}
						assertHeld(selectedCtx, pool, sql)
						requestSHAs = append(requestSHAs, sha)
						outcomes = append(outcomes, result)
						return result
					}
					if retry == 0 {
						target := call(HostColdPhaseRequest{Operation: "cold-b0-target", LuaSHA256: luaSHA, Namespace: "host-selected-redis", Shard: "lightpanda-b0", Cohort: "c1"})
						intentBody := hostPhaseTestIntent(t, info, previous, prepared, target)
						if restore {
							var spec queue.ColdTransitionSpec
							if json.Unmarshal(intentBody, &spec) != nil {
								t.Fatal("restoration original intent")
							}
							spec.PreviousB0ReceiptSHA256 = hostPhaseRetainTestInput(t, state, hostForwardRestorationPriorReceipt(source, previous))
							intentBody, _ = json.Marshal(spec)
						}
						intent := hostPhaseRetainTestInput(t, state, intentBody)
						begun := call(HostColdPhaseRequest{Operation: "cold-begin", PredecessorSHA256: hostPhaseResultSHA(t, target), IntentSHA256: intent, TargetSHA256: target.Native.B0TargetSHA256, LuaSHA256: luaSHA})
						reserved := call(HostColdPhaseRequest{Operation: "cold-reserve", PredecessorSHA256: hostPhaseResultSHA(t, begun), IntentSHA256: intent})
						// Publication can leave this exact candidate plan active. Retire
						// only our reservation after all proof/conservation reads and
						// backend scopes finish, before the next owned fixture starts.
						// The private pipeline's initial plan has separate cleanup.
						planSHA, epoch := reserved.Native.PlanSHA256, reserved.Native.RoutingEpoch
						t.Cleanup(func() {
							cleanup, done := context.WithTimeout(context.Background(), 10*time.Second)
							defer done()
							if _, err := f.pg.Exec(cleanup, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND routing_epoch=$2 AND source_revision=$3 AND state='active'", planSHA, epoch, source); err != nil {
								t.Error("owned joined candidate plan cleanup", err)
							}
						})
						inspected := call(HostColdPhaseRequest{Operation: "cold-inspect", PredecessorSHA256: hostPhaseResultSHA(t, reserved), IntentSHA256: intent})
						if inspected.Native.RoutingEpoch != reserved.Native.RoutingEpoch || inspected.Native.PlanSHA256 != reserved.Native.PlanSHA256 {
							t.Fatal("joined inspection changed reservation")
						}
						if forward {
							producerProof, stopProducer = startHostForwardInstalledProducer(t, ctx, source, proof.Image, proof.Architecture, selectedURL, reserved.Native.RoutingEpoch, lua)
							producerProof["selected_redis_endpoint_sha256"] = info.RedisEndpointSHA256
							producerProof["operator_client_uid"] = 0
							producerProof["synthetic_prior_cleanup_digests"] = true
							forwardBeforeBootstrap = redisBefore
							producerProof["bootstrap"] = initializeHostForwardFixtureTerminal(t, selectedCtx, f, forwardSeed, reserved.Native.RoutingEpoch, lua)
							assertHeld(selectedCtx, pool, sql)
							redisBefore = fullColdExecutableRedisSnapshot(t, f)
							forwardRequest := queue.ColdB0ForwardRequest{IntentSHA256: intent, SourceRevision: source, RoutingEpoch: reserved.Native.RoutingEpoch, OrdinaryPlanSHA256: reserved.Native.PlanSHA256}
							encoded, _ := json.Marshal(forwardRequest)
							forwardSHA := hostPhaseRetainTestInput(t, state, encoded)
							req := HostColdPhaseRequest{Operation: "cold-b0-forward-plan", PredecessorSHA256: hostPhaseResultSHA(t, inspected), IntentSHA256: intent, TargetSHA256: target.Native.B0TargetSHA256, LuaSHA256: luaSHA, RoutingEpoch: reserved.Native.RoutingEpoch, PlanSHA256: reserved.Native.PlanSHA256, ForwardRequestSHA256: forwardSHA}
							planned := call(req)
							validateHostForwardSeedPlan(t, forwardSeed, planned.Native.B0ForwardPlan)
							if !reflect.DeepEqual(redisBefore, fullColdExecutableRedisSnapshot(t, f)) {
								t.Fatal("producer preview changed Redis values")
							}
							req.Operation, req.ForwardPlanSHA256, req.PredecessorSHA256 = "cold-b0-forward-retain", planned.Native.B0ForwardPlanSHA256, hostPhaseResultSHA(t, planned)
							retained := call(req)
							forwardPreview = fullColdExecutableRedisSnapshot(t, f)
							if !reflect.DeepEqual(redisBefore, forwardPreview) {
								t.Fatal("producer retention changed Redis values")
							}
							req.Operation, req.PredecessorSHA256 = "cold-b0-forward-apply", hostPhaseResultSHA(t, retained)
							applied := call(req)
							req.Operation, req.PredecessorSHA256, req.TargetSHA256, req.LuaSHA256 = "cold-b0-forward-inspect", hostPhaseResultSHA(t, applied), "", ""
							observed := call(req)
							req.TargetSHA256, req.LuaSHA256, req.ForwardReceiptSHA256 = target.Native.B0TargetSHA256, luaSHA, observed.Native.B0ForwardReceiptSHA256
							for _, operation := range []string{"cold-forward-prepare", "cold-forward-publish", "cold-forward-activate"} {
								req.Operation, req.PredecessorSHA256 = operation, hostPhaseResultSHA(t, observed)
								observed = call(req)
							}
							verifyHostForwardTransferredQueue(t, ctx, f, forwardSeed, planned.Native.B0ForwardPlan, reserved.Native.RoutingEpoch)
							forwardAfter = fullColdExecutableRedisSnapshot(t, f)
							assertHostForwardUnrelatedValues(t, redisBefore, forwardAfter, forwardSeed)
							if restore {
								verifyHostForwardProducerStillOwned(t, producerProof)
								stopProducer()
								if _, err := os.Stat("/proc/" + strconv.Itoa(producerProof["pid"].(int))); !os.IsNotExist(err) {
									t.Fatal("owned candidate producer survived stop")
								}
								if clientCount() != originalBaselineClients {
									t.Fatal("candidate producer connection survived stop")
								}
								restoration = runHostForwardRestoration(t, selectedCtx, f, state, info, target, observed, intent, previous, forwardSeed, forwardAfter, call)
							}
						}
					} else {
						for n, sha := range requestSHAs {
							if restore && n != 0 && n != 2 && n != 13 && n != 16 && n != 20 {
								continue
							}
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
			if forward && !restore && retry == 0 {
				baselineClients = clientCount()
				if baselineClients <= originalBaselineClients {
					t.Fatal("native producer did not own a Redis connection")
				}
			} else if clientCount() != baselineClients {
				t.Fatal("completed selected scope retained Redis connections")
			}
			if forward && !restore && !reflect.DeepEqual(forwardAfter, fullColdExecutableRedisSnapshot(t, f)) {
				t.Fatal("exact forward retry changed Redis values")
			}
			if restore && !reflect.DeepEqual(restoration.RedisAfter, fullColdExecutableRedisSnapshot(t, f)) {
				t.Fatal("historical restoration retry changed complete Redis values")
			}
			if _, err := InspectHostColdPhaseContext(escaped, escapedPool); err == nil {
				t.Fatal("escaped selected phase context admitted")
			}
			if result, err := RunHostColdPhase(escaped, escapedPool, requestSHAs[0]); err == nil || result != nil {
				t.Fatal("escaped selected context ran historical phase")
			}
			if CheckHostMutationScope(mutationCtx) != nil {
				t.Fatal("original outer host scope lost after SQL release")
			}
			assertHostScopeFlock(t, hostMutationLock, true)
			var locks int
			if f.pg.QueryRow(ctx, "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND pid=$1", sqlPIDs[retry]).Scan(&locks) != nil || locks != 0 {
				t.Fatal("joined callback retained SQL barriers")
			}
		}
		if len(sqlPIDs) != 2 || sqlPIDs[0] <= 0 || sqlPIDs[0] == sqlPIDs[1] {
			t.Fatal("exact retry did not use a new private SQL backend")
		}
		expectedEpoch := outcomes[2].Native.RoutingEpoch
		if restore {
			expectedEpoch = restoration.RetirementEpoch
		}
		if f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&lastEpoch) != nil || lastEpoch != expectedEpoch {
			t.Fatal("joined retry reserved another epoch")
		}
		if !reflect.DeepEqual(canonicalBefore, coldExecutableCanonicalSnapshot(t, f)) || !forward && !reflect.DeepEqual(redisBefore, fullColdExecutableRedisSnapshot(t, f)) {
			t.Fatal("joined cold phase changed canonical rows or complete Redis values")
		}
		if forward {
			if !restore {
				verifyHostForwardProducerStillOwned(t, producerProof)
			}
			stopProducer()
			if clientCount() != originalBaselineClients {
				t.Fatal("stopped owned producer retained Redis connections")
			}
			expectedInterruptions := []string{"cold-b0-forward-apply", "cold-forward-publish"}
			if restore {
				expectedInterruptions = append(expectedInterruptions, "cold-reversal-reserve", "cold-b0-rollback-restore")
			}
			if !reflect.DeepEqual(interrupted, expectedInterruptions) {
				t.Fatal("forward effect/result seams not exercised")
			}
		}
		if CheckHostMutationScope(mutationCtx) != nil {
			t.Fatal("outer host scope lost before lifecycle completion")
		}
		assertHostScopeFlock(t, hostMutationLock, true)
		return nil
	}); err != nil {
		t.Fatal("joined outer host lifecycle", err)
	}
	assertHostScopeFlock(t, hostMutationLock, false)
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
	inputSHAs := []string{luaSHA, outcomes[0].Native.B0TargetSHA256, outcomes[1].Native.IntentSHA256}
	if forward {
		var request HostColdPhaseRequest
		b, err := os.ReadFile(filepath.Join(state, "cold-request-"+requestSHAs[4]+".json"))
		if err != nil || json.Unmarshal(b, &request) != nil {
			t.Fatal("forward retained typed request")
		}
		inputSHAs = append(inputSHAs, request.ForwardRequestSHA256, outcomes[4].Native.B0ForwardPlanSHA256)
	}
	if restore {
		for _, requestSHA := range requestSHAs {
			var request HostColdPhaseRequest
			raw, err := os.ReadFile(filepath.Join(state, "cold-request-"+requestSHA+".json"))
			if err != nil || json.Unmarshal(raw, &request) != nil {
				t.Fatal("restoration retained typed request")
			}
			for _, sha := range []string{request.ReversalSHA256, request.RestoreRequestSHA256, request.B0RollbackPlanSHA256, request.OrdinaryRequestSHA256, request.OrdinaryRestorationPlanSHA256} {
				if sha != "" {
					inputSHAs = append(inputSHAs, sha)
				}
			}
		}
		inputSHAs = append(inputSHAs, hostDigest(hostForwardRestorationPriorReceipt(source, previous)))
	}
	for _, sha := range inputSHAs {
		if !planPattern.MatchString(sha) {
			t.Fatal("joined bounded retained input identity")
		}
		body, err := os.ReadFile(filepath.Join(state, "cold-input-"+sha))
		if err != nil || hostDigest(body) != sha {
			t.Fatal("joined exact retained input")
		}
		phaseInputs[sha] = string(body)
	}
	joinedProof := map[string]any{"version": "jobseek.fixture.host-selected-redis-journal/v1", "source_revision": source, "architecture": runtime.GOARCH, "installed_image_id": proof.Image, "installed_binary_sha256": proof.Binary, "selected_redis_endpoint_sha256": originalInfo.RedisEndpointSHA256, "redis_instance_sha256": originalInfo.RedisInstanceSHA256, "cold_attestation_sha256": originalInfo.ColdAttestationSHA256, "phase_request_sha256": requestSHAs, "reservation_epoch": outcomes[2].Native.RoutingEpoch, "sql_backend_pids": sqlPIDs, "connection_receipts_sha256": retained, "phase_context": originalInfo, "host_request_sha256": hostDigest(body), "phase_inputs": phaseInputs, "phase_records": phaseRecords, "connection_receipts": connectionReceipts, "released_redis_client_count": baselineClients, "canonical_and_complete_redis_values_conserved": true, "runtime_admission": false, "scope": "actual native library callback with installed image preflight, sleeping consumer stand-ins and private SQL/Redis; complete installed cold CLI/phase graph/producer exclusion/runtime admission unproven"}
	joinedProof["original_outer_host_flock_held_across_SQL_release_and_exact_backend_retry"] = true
	proofPath := os.Getenv("JOBSEEK_CRAWLER_RELEASE_HOST_SELECTED_REDIS_PROOF")
	if forward {
		joinedProof["version"] = "jobseek.fixture.host-selected-redis-forward/v1"
		joinedProof["producer"] = producerProof
		joinedProof["seed"] = forwardSeed
		joinedProof["redis_before"] = redisBefore
		joinedProof["redis_before_bootstrap"] = forwardBeforeBootstrap
		joinedProof["redis_after"] = forwardAfter
		joinedProof["redis_after_initial_preview"] = forwardPreview
		joinedProof["canonical_before"] = canonicalBefore
		joinedProof["canonical_after"] = coldExecutableCanonicalSnapshot(t, f)
		joinedProof["interrupted_after_native_effect"] = interrupted
		joinedProof["canonical_and_complete_redis_values_conserved"] = false
		joinedProof["canonical_rows_and_unrelated_redis_values_conserved"] = true
		joinedProof["exact_retry_complete_redis_values_conserved"] = true
		joinedProof["producer_stopped_and_connections_released"] = true
		joinedProof["released_redis_client_count"] = originalBaselineClients
		joinedProof["scope"] = "actual native library host callback, installed preflight and source/image-bound UID10001 native producer through B0 transfer and ordinary publication, effect/result interruption recovery and exact retry; sleeping ordinary consumers, full installed cold CLI/reversal graph/startup/runtime admission unproven"
		proofPath = os.Getenv("JOBSEEK_CRAWLER_RELEASE_HOST_FORWARD_JOURNAL_PROOF")
	}
	if restore {
		joinedProof["version"] = "jobseek.fixture.host-selected-redis-restoration/v1"
		joinedProof["restoration"] = restoration
		joinedProof["retirement_epoch"] = lastEpoch
		joinedProof["historical_retry_phase_indices"] = []int{0, 2, 13, 16, 20}
		joinedProof["synthetic_prior_B0_receipt"] = string(hostForwardRestorationPriorReceipt(source, previous))
		joinedProof["scope"] = "actual native library/installed preflight and UID10001 candidate producer through 21 forward/active reversal/nonempty restoration phases and selected exact retries under original host flock; sleeping consumers and synthetic prior E receipt, real restoration SIGKILL, producer sentinel cleanup/reactivation/finalization/startup/runtime admission unproven"
		proofPath = os.Getenv("JOBSEEK_CRAWLER_RELEASE_HOST_RESTORATION_JOURNAL_PROOF")
	}
	encoded, err := json.MarshalIndent(joinedProof, "", "  ")
	if err != nil || proofPath == "" || os.WriteFile(proofPath, append(encoded, '\n'), 0600) != nil {
		t.Fatal("joined fixture proof retention")
	}
	if restore {
		t.Log("actual installed UID10001 candidate N forward and active reversal/restoration journal completes 21 phases; exact R and source transfer receipt bind nonempty task retirement, canonical legacy schedules/hash/interval, unchanged canonical SQL and unrelated Redis; source producer stopped before restore, four returned-effect seams recover, new SQL backend selected historical retries preserve R; prior E receipt and consumers are fixtures, sentinel cleanup/reactivation/finalization/startup/runtime admission unproven")
	} else if forward {
		t.Log("actual host callback and selected official Redis joined installed UID10001 native producer to eleven retained phases through B0 task transfer and ordinary publication; actual native bootstrap activation and retained terminal work, exact schedules/hints/task payloads and pruned work verified, canonical rows and unrelated Redis values conserved; transfer and publication effect/result interruption recovered, new SQL backend exact retry kept epoch/results/complete Redis values, producer and owned connections released; native library plus installed preflight/producer only, complete installed cold CLI/reversal/startup/runtime admission unproven")
	} else {
		t.Log("actual host callback joined selected Compose/image/consumer execution and official Redis PID/listener inode to native target/intent/reservation/inspection journal effects; original mutation flock and all SQL barriers held across independent commits; new backend exact retry kept endpoint/incarnation, epoch and results, canonical and complete Redis values conserved, missing/escaped scope refused and resources released; installed preflight plus native library/sleeping consumer fixture only, complete cold CLI/phase graph/producer exclusion/runtime admission unproven")
	}
}
