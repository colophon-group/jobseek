package worker

import (
	"context"
	"encoding/json"
	"errors"
	"io/fs"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5/pgxpool"
)

type hostPhaseTestDriver struct {
	Binding     queue.HostColdSQLBinding `json:"binding"`
	Releases    []HostReleaseRequest     `json:"releases"`
	ColdSHA     string                   `json:"cold_sha256"`
	Database    string                   `json:"database"`
	Redis       string                   `json:"redis"`
	RequestSHA  string                   `json:"request_sha256"`
	CrashPhase  string                   `json:"crash_phase"`
	EndpointSHA string                   `json:"endpoint_sha256"`
	InstanceSHA string                   `json:"instance_sha256"`
}

func runHostPhaseTestDriver(ctx context.Context, state string, pool *pgxpool.Pool, d hostPhaseTestDriver, fn func(context.Context, *hostStore) error) error {
	lock, err := acquireHostLock(ctx, filepath.Join(state, "mutation.lock"))
	if err != nil {
		return err
	}
	defer lock.Close()
	store, err := openHostStore(state)
	if err != nil {
		return err
	}
	defer store.Close()
	return queue.WithHostColdSQL(ctx, pool, d.Binding, func(scoped context.Context, sql *queue.HostColdSQL) error {
		return withHostColdPhaseScope(scoped, store, pool, sql, d.Binding, d.Releases, d.ColdSHA, func() error {
			if lock.verify() != nil {
				return errHostPreflight
			}
			return store.verify()
		}, func(scoped context.Context) error {
			// These are explicit fixture bindings, not actual selected Docker or
			// kernel endpoint evidence. Production uses WithSelectedHostColdRedis.
			client, err := queue.Open(d.Redis, queue.Settings{LeaseTTL: time.Minute, MaxDomains: 10})
			if err != nil {
				return err
			}
			defer client.Close()
			host := scoped.Value(hostColdPhaseKey{}).(*hostColdPhaseScope)
			return withHostColdRedisScope(scoped, host, client, d.EndpointSHA, func() error {
				instance, err := client.RedisInstanceSHA256(scoped)
				if err != nil || instance != d.InstanceSHA {
					return errHostPreflight
				}
				return nil
			}, func(scoped context.Context) error { return fn(scoped, store) })
		})
	})
}

func hostPhaseTestFixture(t *testing.T) (nativePipelineFixture, hostPhaseTestDriver, string, int64, string, string) {
	t.Helper()
	f := privatePipelineFixture(t)
	ctx := context.Background()
	source := ordinaryFixtureSourceRevision(t)
	if _, err := f.pg.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE state='active'"); err != nil || f.r.Del(ctx, "ordinary:ownership:active").Err() != nil {
		t.Fatal("private setup owner retirement")
	}
	var previous int64
	if f.pg.QueryRow(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')").Scan(&previous) != nil {
		t.Fatal("private initial epoch")
	}
	stage, err := queue.OpenAuthority(ctx, f.dsn, f.client, previous)
	if err != nil {
		t.Fatal("private staging authority")
	}
	prepared, err := stage.StageGreenhouseOwnership(ctx, source, []string{f.board})
	stage.Close()
	if err != nil {
		t.Fatal("private prepared ownership")
	}
	b0 := fixtureID(t)
	if _, err := f.pg.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser)
 VALUES($1::uuid,$2::uuid,'browser-use-careers','https://jobs.example.test/careers','api_sniffer','{}',60,24,'',false,true)`, b0, f.company); err != nil {
		t.Fatal("private B0 board")
	}
	if f.r.HSet(ctx, "board:"+b0, map[string]string{"board_slug": "browser-use-careers", "board_url": "https://jobs.example.test/careers", "crawler_type": "api_sniffer", "company_id": f.company, "metadata": "{}", "check_interval_minutes": "60", "scrape_interval_hours": "24", "throttle_key": "", "domain": "jobs.example.test", "monitor_needs_browser": "0", "scraper_needs_browser": "1"}).Err() != nil {
		t.Fatal("private B0 projection")
	}
	t.Cleanup(func() {
		// privatePipelineFixture rejects every nonlocal or non-test database.
		if _, err := f.pg.Exec(context.Background(), "TRUNCATE crawler_ownership_restoration_completion,crawler_ownership_restoration_publication,crawler_ownership_restoration_finalization,crawler_ownership_b0_reactivation_completion,crawler_ownership_b0_reactivation,crawler_ownership_ordinary_restoration,crawler_ownership_b0_forward_completion,crawler_ownership_b0_forward,crawler_ownership_b0_restoration,crawler_ownership_reversal,crawler_ownership_transition,crawler_ownership_b0_target"); err != nil {
			t.Error("private retained journal cleanup")
		}
	})
	state := hostPrivateDirectory(t)
	d := hostPhaseTestDriver{Binding: queue.HostColdSQLBinding{SourceRevision: source, RequestSHA256: strings.Repeat("b", 64), ContainmentIntentSHA256: strings.Repeat("c", 64)}, ColdSHA: strings.Repeat("d", 64), Database: f.dsn, Redis: "unix://" + f.r.Options().Addr}
	d.EndpointSHA = strings.Repeat("9", 64)
	d.InstanceSHA, err = f.client.RedisInstanceSHA256(ctx)
	if err != nil {
		t.Fatal("private Redis incarnation")
	}
	binding, _ := json.Marshal(struct{ EndpointSHA, InstanceSHA string }{d.EndpointSHA, d.InstanceSHA})
	if os.WriteFile(filepath.Join(state, "test-redis-binding.json"), binding, 0600) != nil {
		t.Fatal("private Redis binding fixture")
	}
	for n, role := range []string{"active", "incoming", "rollback"} {
		d.Releases = append(d.Releases, HostReleaseRequest{Role: role, FileEvidenceSHA256: strings.Repeat(string(rune('3'+n)), 64)})
	}
	lua, err := os.ReadFile("../../src/lua/lightpanda_b0_queue.lua")
	if err != nil {
		t.Fatal("reviewed fixture Lua")
	}
	luaSHA := hostPhaseRetainTestInput(t, state, lua)
	return f, d, state, previous, prepared.SHA256(), luaSHA
}

func hostPhaseRetainTestInput(t *testing.T, state string, body []byte) string {
	t.Helper()
	s, err := openHostStore(state)
	if err != nil {
		t.Fatal("private test input store")
	}
	defer s.Close()
	sha := hostDigest(body)
	if s.retain("cold-input-"+sha, body, nil) != nil {
		t.Fatal("private hashed input retention")
	}
	return sha
}

func hostPhaseRetainTestRequest(t *testing.T, state string, r HostColdPhaseRequest) string {
	t.Helper()
	s, err := openHostStore(state)
	if err != nil {
		t.Fatal("private test request store")
	}
	defer s.Close()
	if r.RedisEndpointSHA256 == "" || r.RedisInstanceSHA256 == "" {
		body, err := s.read("test-redis-binding.json", false)
		var binding struct{ EndpointSHA, InstanceSHA string }
		if err != nil || canonicalHostDecode(body, &binding) != nil {
			t.Fatal("private Redis request binding")
		}
		if r.RedisEndpointSHA256 == "" {
			r.RedisEndpointSHA256 = binding.EndpointSHA
		}
		if r.RedisInstanceSHA256 == "" {
			r.RedisInstanceSHA256 = binding.InstanceSHA
		}
	}
	body, _ := json.Marshal(r)
	sha := hostDigest(body)
	if s.retain("cold-request-"+sha+".json", body, nil) != nil {
		t.Fatal("private canonical request retention")
	}
	return sha
}

func hostPhaseResultSHA(t *testing.T, r *HostColdPhaseResult) string {
	t.Helper()
	body, err := json.Marshal(r)
	if err != nil || r == nil {
		t.Fatal("missing retained phase result")
	}
	return hostDigest(body)
}

func hostPhaseTestIntent(t *testing.T, info HostColdPhaseContext, previous int64, prepared string, target *HostColdPhaseResult) []byte {
	t.Helper()
	if target == nil || target.Native == nil {
		t.Fatal("missing captured target")
	}
	s := queue.ColdTransitionSpec{Version: "jobseek.crawler.cold-transition/v1", TransitionID: fixtureID(t), SourceRevision: info.Binding.SourceRevision, PreviousEpoch: previous, PreparedPlanSHA256: prepared, TargetB0ManifestSHA256: target.Native.B0TargetSHA256, ActiveReleaseSHA256: info.ActiveReleaseSHA256, TargetReleaseSHA256: info.TargetReleaseSHA256, RollbackReleaseSHA256: info.RollbackReleaseSHA256, ColdAttestationSHA256: info.ColdAttestationSHA256}
	body, err := json.Marshal(s)
	if err != nil {
		t.Fatal("private bound intent")
	}
	return body
}

func TestRealHostColdPhaseJournalRetainsLinearInputsAndOutcomes(t *testing.T) {
	f, d, state, previous, prepared, luaSHA := hostPhaseTestFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	canonical, redisBefore := coldExecutableCanonicalSnapshot(t, f), fullColdExecutableRedisSnapshot(t, f)
	var info HostColdPhaseContext
	var escaped context.Context
	call := func(sha string, hook func(string) error) (*HostColdPhaseResult, error) {
		t.Helper()
		var result *HostColdPhaseResult
		err := runHostPhaseTestDriver(ctx, state, f.pg, d, func(scoped context.Context, store *hostStore) error {
			escaped = scoped
			current, err := InspectHostColdPhaseContext(scoped, f.pg)
			if err != nil || current.RuntimeAdmission {
				t.Fatal("live private phase context")
			}
			if info.Version != "" && info != current {
				t.Fatal("backend retry changed stable cold bindings")
			}
			info = current
			wrong := d.Binding
			wrong.ContainmentIntentSHA256 = strings.Repeat("f", 64)
			if queue.CheckHostColdSQLBinding(scoped, f.pg, wrong) == nil {
				t.Fatal("different host intent adopted SQL scope")
			}
			result, err = runHostColdPhase(scoped, f.pg, sha, hook)
			return err
		})
		return result, err
	}
	targetRequest := HostColdPhaseRequest{Version: "jobseek.crawler-host-cold-request/v2", Binding: d.Binding, Operation: "cold-b0-target", PreviousEpoch: previous, LuaSHA256: luaSHA, Namespace: "host-phase", Shard: "lightpanda-b0", Cohort: "c1"}
	targetSHA := hostPhaseRetainTestRequest(t, state, targetRequest)
	if err := runHostPhaseTestDriver(ctx, state, f.pg, d, func(scoped context.Context, store *hostStore) error {
		without := context.WithValue(scoped, hostColdRedisKey{}, nil)
		if result, err := RunHostColdPhase(without, f.pg, targetSHA); err == nil || result != nil {
			t.Fatal("host SQL scope alone adopted a caller Redis connection")
		}
		if WithSelectedHostColdRedis(without, f.pg, func(context.Context) error {
			t.Fatal("fixture hashes granted production selected endpoint")
			return nil
		}) == nil {
			t.Fatal("missing actual resolver admitted caller endpoint")
		}
		return nil
	}); err != nil {
		t.Fatal("unscoped Redis rejection", err)
	}
	for _, fault := range []string{"endpoint", "instance"} {
		bad := targetRequest
		bad.RedisEndpointSHA256 = d.EndpointSHA
		bad.RedisInstanceSHA256 = d.InstanceSHA
		if fault == "endpoint" {
			bad.RedisEndpointSHA256 = strings.Repeat("8", 64)
		} else {
			bad.RedisInstanceSHA256 = strings.Repeat("8", 64)
		}
		if result, err := call(hostPhaseRetainTestRequest(t, state, bad), nil); err == nil || result != nil {
			t.Fatal("different Redis endpoint/incarnation adopted phase", fault)
		}
	}
	target, err := call(targetSHA, nil)
	if err != nil || target.Outcome != "completed" {
		t.Fatal("retained target", err)
	}
	intent := hostPhaseRetainTestInput(t, state, hostPhaseTestIntent(t, info, previous, prepared, target))
	beginRequest := HostColdPhaseRequest{Version: targetRequest.Version, Binding: d.Binding, PredecessorSHA256: hostPhaseResultSHA(t, target), Operation: "cold-begin", PreviousEpoch: previous, IntentSHA256: intent, TargetSHA256: target.Native.B0TargetSHA256, LuaSHA256: luaSHA}
	beginSHA := hostPhaseRetainTestRequest(t, state, beginRequest)
	var retained bool
	for _, field := range []string{"active", "incoming", "rollback", "cold"} {
		var bad queue.ColdTransitionSpec
		if json.Unmarshal(hostPhaseTestIntent(t, info, previous, prepared, target), &bad) != nil {
			t.Fatal("bound rejection fixture")
		}
		switch field {
		case "active":
			bad.ActiveReleaseSHA256 = strings.Repeat("e", 64)
		case "incoming":
			bad.TargetReleaseSHA256 = strings.Repeat("e", 64)
		case "rollback":
			bad.RollbackReleaseSHA256 = strings.Repeat("e", 64)
		case "cold":
			bad.ColdAttestationSHA256 = strings.Repeat("e", 64)
		}
		body, _ := json.Marshal(bad)
		request := beginRequest
		request.IntentSHA256 = hostPhaseRetainTestInput(t, state, body)
		if result, err := call(hostPhaseRetainTestRequest(t, state, request), nil); err == nil || result != nil {
			t.Fatal("unverified release/cold binding admitted", field)
		}
		if f.pg.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM crawler_ownership_transition WHERE intent_sha256=$1)", request.IntentSHA256).Scan(&retained) != nil || retained {
			t.Fatal("rejected release binding reached native intent")
		}
	}
	// An exact request is durably claimed before any SQL/Redis effect. Another
	// child may not race or replace it even while no result exists yet.
	if result, err := call(beginSHA, func(phase string) error {
		if phase == "phase_intent_retained" {
			return errors.New("fixture interruption")
		}
		return nil
	}); err == nil || result != nil {
		t.Fatal("intent seam did not stop")
	}
	if f.pg.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM crawler_ownership_transition WHERE intent_sha256=$1)", intent).Scan(&retained) != nil || retained {
		t.Fatal("native effect preceded retained phase intent")
	}
	competitor := beginRequest
	// Keep inputs valid so this tests the immutable child claim, not missing IO.
	competitor.IntentSHA256 = hostPhaseRetainTestInput(t, state, hostPhaseTestIntent(t, info, previous, prepared, target))
	competitor.LuaSHA256 = luaSHA
	if result, err := call(hostPhaseRetainTestRequest(t, state, competitor), nil); err == nil || result != nil {
		t.Fatal("competing child replaced retained claim")
	}
	// A native rejection is a durable unresolved event, with no zero-effect
	// claim. Only an exact-input retry event may follow it.
	projection, err := f.r.HGetAll(ctx, "board:"+f.board).Result()
	if err != nil || f.r.Del(ctx, "board:"+f.board).Err() != nil {
		t.Fatal("private transient Redis fixture")
	}
	unresolved, err := call(beginSHA, nil)
	if err != nil || unresolved.Outcome != "unresolved" || unresolved.Native != nil {
		t.Fatal("native rejection not retained", err)
	}
	if f.r.HSet(ctx, "board:"+f.board, projection).Err() != nil {
		t.Fatal("private projection recovery")
	}
	if retry, err := call(beginSHA, nil); err != nil || !reflect.DeepEqual(retry, unresolved) {
		t.Fatal("completed unresolved event repeated effects")
	}
	wrongNext := HostColdPhaseRequest{Version: targetRequest.Version, Binding: d.Binding, PredecessorSHA256: hostPhaseResultSHA(t, unresolved), Operation: "cold-reserve", PreviousEpoch: previous, IntentSHA256: intent}
	if result, err := call(hostPhaseRetainTestRequest(t, state, wrongNext), nil); err == nil || result != nil {
		t.Fatal("unresolved predecessor authorized reservation")
	}
	beginRequest.PredecessorSHA256 = hostPhaseResultSHA(t, unresolved)
	beginSHA = hostPhaseRetainTestRequest(t, state, beginRequest)
	begun, err := call(beginSHA, nil)
	if err != nil || begun.Outcome != "completed" || begun.Native.IntentSHA256 != intent {
		t.Fatal("explicit exact-input retry failed", err)
	}
	reserveRequest := HostColdPhaseRequest{Version: targetRequest.Version, Binding: d.Binding, PredecessorSHA256: hostPhaseResultSHA(t, begun), Operation: "cold-reserve", PreviousEpoch: previous, IntentSHA256: intent}
	reserveSHA := hostPhaseRetainTestRequest(t, state, reserveRequest)
	if result, err := call(reserveSHA, func(phase string) error {
		if phase == "native_effect_returned" {
			return errors.New("fixture interruption")
		}
		return nil
	}); err == nil || result != nil {
		t.Fatal("post-effect seam did not stop")
	}
	var epoch int64
	if f.pg.QueryRow(ctx, "SELECT routing_epoch FROM crawler_ownership_transition WHERE intent_sha256=$1", intent).Scan(&epoch) != nil || epoch <= previous {
		t.Fatal("reservation did not commit before interrupted result")
	}
	// A hard-linked result without a completion index must be recovered by its
	// exact request before a later request may name it as predecessor.
	if result, err := call(reserveSHA, func(phase string) error {
		if phase == "file_linked" {
			return errors.New("fixture interruption")
		}
		return nil
	}); err == nil || result != nil {
		t.Fatal("result publication seam did not stop")
	}
	s, err := openHostStore(state)
	if err != nil {
		t.Fatal(err)
	}
	body, err := s.read("cold-result-"+reserveSHA+".json", true)
	if err != nil {
		t.Fatal("missing retained pending result")
	}
	inspection := HostColdPhaseRequest{Version: targetRequest.Version, Binding: d.Binding, PredecessorSHA256: hostDigest(body), Operation: "cold-inspect", PreviousEpoch: previous, IntentSHA256: intent}
	inspectionSHA := hostPhaseRetainTestRequest(t, state, inspection)
	if _, err := s.read("cold-completed-"+reserveSHA+".json", false); !errors.Is(err, fs.ErrNotExist) {
		t.Fatal("completion published before result seam")
	}
	s.Close()
	if result, err := call(inspectionSHA, nil); err == nil || result != nil {
		t.Fatal("uncompleted predecessor admitted inspection")
	}
	reserved, err := call(reserveSHA, nil)
	if err != nil || reserved.Outcome != "completed" || reserved.Native.RoutingEpoch != epoch {
		t.Fatal("exact reservation/result recovery", err)
	}
	inspected, err := call(inspectionSHA, nil)
	if err != nil || inspected.Outcome != "completed" || inspected.Native.RoutingEpoch != epoch || inspected.Native.PlanSHA256 != reserved.Native.PlanSHA256 {
		t.Fatal("retained inspection binding", err)
	}
	if old, err := call(targetSHA, nil); err != nil || !reflect.DeepEqual(old, target) {
		t.Fatal("historical completed retry changed target", err)
	}
	if _, err := RunHostColdPhase(escaped, f.pg, inspectionSHA); err == nil {
		t.Fatal("escaped host context granted a later phase")
	}
	if _, err := InspectHostColdPhaseContext(escaped, f.pg); err == nil {
		t.Fatal("returned host evidence granted live context")
	}
	if f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&previous) != nil || previous != epoch {
		t.Fatal("retry consumed an extra epoch")
	}
	if coldExecutableCanonicalSnapshot(t, f) != canonical || !reflect.DeepEqual(redisBefore, fullColdExecutableRedisSnapshot(t, f)) {
		t.Fatal("first cold phase journal changed canonical or complete Redis values")
	}
	t.Log("actual private PostgreSQL/Redis cold phase journal retained intent before effects, linear predecessor completion and unresolved outcomes; explicit exact-input retry and post-reservation/result-link recovery reused the same epoch; canonical and complete Redis values conserved; private flock and fixture release/container hashes only, full host driver and runtime admission unproven")
}

func TestHostColdPhaseRejectsUnscopedAndNoncanonicalRequests(t *testing.T) {
	if _, err := RunHostColdPhase(context.Background(), nil, strings.Repeat("a", 64)); err == nil {
		t.Fatal("unscoped journal admitted")
	}
	b := queue.HostColdSQLBinding{SourceRevision: strings.Repeat("a", 40), RequestSHA256: strings.Repeat("b", 64), ContainmentIntentSHA256: strings.Repeat("c", 64)}
	r := HostColdPhaseRequest{Version: "jobseek.crawler-host-cold-request/v2", Binding: b, Operation: "cold-b0-target", PreviousEpoch: 1, LuaSHA256: strings.Repeat("d", 64), Namespace: "fixture", Shard: "lightpanda-b0", Cohort: "c1"}
	r.RedisEndpointSHA256, r.RedisInstanceSHA256 = strings.Repeat("e", 64), strings.Repeat("f", 64)
	body, _ := json.Marshal(r)
	if _, err := decodeHostColdPhase(body, hostDigest(body), b); err != nil {
		t.Fatal("canonical phase refused")
	}
	for _, mutate := range []func([]byte) []byte{
		func(p []byte) []byte { return append(p, '\n') },
		func(p []byte) []byte { return append([]byte(`{"endpoint":"redis://caller",`), p[1:]...) },
		func(p []byte) []byte {
			return []byte(strings.Replace(string(p), `"previous_epoch":1`, `"previous_epoch":1,"previous_epoch":1`, 1))
		},
		func(p []byte) []byte {
			return []byte(strings.Replace(string(p), `"operation":"cold-b0-target"`, `"operation":"cold-activate"`, 1))
		},
		func(p []byte) []byte {
			return []byte(strings.Replace(string(p), strings.Repeat("d", 64), "/arbitrary/input", 1))
		},
	} {
		bad := mutate(append([]byte{}, body...))
		if _, err := decodeHostColdPhase(bad, hostDigest(bad), b); err == nil {
			t.Fatal("untrusted phase request accepted")
		}
	}
}

func TestHostColdPhaseSIGKILLHelper(t *testing.T) {
	state := os.Getenv("JOBSEEK_HOST_COLD_PHASE_TEST_DIRECTORY")
	if state == "" {
		return
	}
	// The helper is a private test-binary driver, not an installed CLI mode.
	// It opens only this test's local database and owned Unix Redis fixture.
	s, err := openHostStore(state)
	if err != nil {
		os.Exit(2)
	}
	body, err := s.read("test-driver.json", false)
	s.Close()
	var d hostPhaseTestDriver
	if err != nil || canonicalHostDecode(body, &d) != nil || !planPattern.MatchString(d.RequestSHA) || (d.CrashPhase != "native_effect_returned" && d.CrashPhase != "file_linked") {
		os.Exit(2)
	}
	u, err := url.Parse(d.Database)
	if err != nil || !strings.HasSuffix(u.Path, "_ordinary_worker_test") || (u.Hostname() != "127.0.0.1" && u.Hostname() != "localhost") || !strings.HasPrefix(d.Redis, "unix:///tmp/jow-") {
		os.Exit(2)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	pool, err := pgxpool.New(ctx, d.Database)
	if err != nil {
		os.Exit(2)
	}
	defer pool.Close()
	_ = runHostPhaseTestDriver(ctx, state, pool, d, func(scoped context.Context, store *hostStore) error {
		_, err := runHostColdPhase(scoped, pool, d.RequestSHA, func(phase string) error {
			if phase == d.CrashPhase {
				if syscall.Kill(os.Getpid(), syscall.SIGKILL) != nil {
					os.Exit(3)
				}
				select {}
			}
			return nil
		})
		return err
	})
	os.Exit(4)
}

func TestRealHostColdPhaseJournalSIGKILLRecoversSameReservation(t *testing.T) {
	for _, seam := range []string{"native_effect_returned", "file_linked"} {
		t.Run(seam, func(t *testing.T) {
			f, d, state, previous, prepared, luaSHA := hostPhaseTestFixture(t)
			ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
			defer cancel()
			canonical, redisBefore := coldExecutableCanonicalSnapshot(t, f), fullColdExecutableRedisSnapshot(t, f)
			var info HostColdPhaseContext
			call := func(r HostColdPhaseRequest) *HostColdPhaseResult {
				t.Helper()
				sha := hostPhaseRetainTestRequest(t, state, r)
				var result *HostColdPhaseResult
				if err := runHostPhaseTestDriver(ctx, state, f.pg, d, func(scoped context.Context, store *hostStore) error {
					var err error
					info, err = InspectHostColdPhaseContext(scoped, f.pg)
					if err != nil {
						return err
					}
					result, err = RunHostColdPhase(scoped, f.pg, sha)
					return err
				}); err != nil || result == nil || result.Outcome != "completed" {
					t.Fatal("private phase execution", err)
				}
				return result
			}
			targetRequest := HostColdPhaseRequest{Version: "jobseek.crawler-host-cold-request/v2", Binding: d.Binding, Operation: "cold-b0-target", PreviousEpoch: previous, LuaSHA256: luaSHA, Namespace: "host-phase-kill", Shard: "lightpanda-b0", Cohort: "c1"}
			target := call(targetRequest)
			intent := hostPhaseRetainTestInput(t, state, hostPhaseTestIntent(t, info, previous, prepared, target))
			begun := call(HostColdPhaseRequest{Version: targetRequest.Version, Binding: d.Binding, PredecessorSHA256: hostPhaseResultSHA(t, target), Operation: "cold-begin", PreviousEpoch: previous, IntentSHA256: intent, TargetSHA256: target.Native.B0TargetSHA256, LuaSHA256: luaSHA})
			reserve := HostColdPhaseRequest{Version: targetRequest.Version, Binding: d.Binding, PredecessorSHA256: hostPhaseResultSHA(t, begun), Operation: "cold-reserve", PreviousEpoch: previous, IntentSHA256: intent}
			d.RequestSHA, d.CrashPhase = hostPhaseRetainTestRequest(t, state, reserve), seam
			s, err := openHostStore(state)
			if err != nil {
				t.Fatal("private child store")
			}
			body, _ := json.Marshal(d)
			if s.retain("test-driver.json", body, nil) != nil {
				t.Fatal("private child request")
			}
			s.Close()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestHostColdPhaseSIGKILLHelper$")
			cmd.Env = append(os.Environ(), "JOBSEEK_HOST_COLD_PHASE_TEST_DIRECTORY="+state)
			err = cmd.Run()
			var exit *exec.ExitError
			if !errors.As(err, &exit) || !exit.ProcessState.Sys().(syscall.WaitStatus).Signaled() || exit.ProcessState.Sys().(syscall.WaitStatus).Signal() != syscall.SIGKILL {
				t.Fatal("native journal child was not killed at retained seam", err)
			}
			lock, err := os.OpenFile(filepath.Join(state, "mutation.lock"), os.O_RDWR, 0)
			if err != nil || syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
				t.Fatal("SIGKILL retained private host flock")
			}
			_ = syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
			lock.Close()
			var epoch int64
			if f.pg.QueryRow(ctx, "SELECT routing_epoch FROM crawler_ownership_transition WHERE intent_sha256=$1", intent).Scan(&epoch) != nil || epoch <= previous {
				t.Fatal("SIGKILL lost committed reservation")
			}
			recovered := call(reserve)
			if recovered.Native.RoutingEpoch != epoch {
				t.Fatal("SIGKILL retry allocated a replacement epoch")
			}
			if duplicate := call(reserve); !reflect.DeepEqual(recovered, duplicate) {
				t.Fatal("completed process retry changed retained result")
			}
			if f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&previous) != nil || previous != epoch {
				t.Fatal("process retry consumed an extra epoch")
			}
			if coldExecutableCanonicalSnapshot(t, f) != canonical || !reflect.DeepEqual(redisBefore, fullColdExecutableRedisSnapshot(t, f)) {
				t.Fatal("SIGKILL reservation retry changed canonical or complete Redis state")
			}
		})
	}
	t.Log("actual Go test-binary SIGKILL after SQL reservation and during protected result hard-link publication released the private host flock and SQL session; exact phase retry retained the original epoch and result, canonical and complete Redis values conserved; fixture role/container hashes only, installed full host cold driver and runtime admission unproven")
}
